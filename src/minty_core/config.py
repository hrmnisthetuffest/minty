from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from minty_core import timefmt
from minty_core._toml import tomli_w, tomllib
from minty_core.configtools import explain_validation
from minty_core.fsutil import atomic_write_text


class ConfigError(Exception):
    """Raised when loading or validating config fails."""
    pass


# Credential-looking names are refused in `extra` (secrets belong in the key store). "tokens" (a count, as in
# max_tokens) is fine; a bare "token" (access_token, auth_token) is not.
_CREDENTIAL_NAME = re.compile(r"key|token(?!s)|secret|password|authorization")


def _check_extra(value: Any, path: str) -> None:
    if value is None:
        raise ValueError(f"extra.{path} must not be null")
    if isinstance(value, dict):
        for k, v in value.items():
            if _CREDENTIAL_NAME.search(str(k).lower()):
                raise ValueError(f"extra.{path + '.' if path else ''}{k}: credentials belong in the key store")
            _check_extra(v, f"{path + '.' if path else ''}{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _check_extra(v, f"{path}[{i}]")


class Limits(BaseModel):
    """A provider's free-tier limits; None = not known (nothing is assumed)."""
    rpm: int | None = Field(None, gt=0)  # requests per minute
    tpm: int | None = Field(None, gt=0)  # tokens per minute
    rpd: int | None = Field(None, gt=0)  # requests per day
    tpd: int | None = Field(None, gt=0)  # tokens per day


# Verified 2026-10 against each provider's published limits. Used when a provider in an older
# config has no limits of its own. Mistral, Gemini and Cerebras publish nothing fixed: left unknown.
KNOWN_LIMITS: list[tuple[str, Callable[[str], bool], Limits]] = [
    ("api.groq.com", lambda model: "gpt-oss" in model, Limits(rpm=30, tpm=8000, rpd=1000, tpd=200000)),
    ("openrouter.ai", lambda model: model.endswith(":free"), Limits(rpm=20, rpd=50)),
    ("integrate.api.nvidia.com", lambda model: True, Limits(rpm=40)),
]


# What each host does with prompts, verified 2026-10: free Gemini, Mistral's
# Experiment plan and OpenRouter's free models may train on prompts; NVIDIA NIM and Cerebras terms are not verified,
# so they stay out of private turns until you check them (set policy_verified = true on that provider).
KNOWN_POLICY: list[tuple[str, Callable[[str], bool], dict]] = [
    ("generativelanguage.googleapis.com", lambda model: True, {"trains": True, "allow": False, "verified": True}),
    ("api.mistral.ai", lambda model: True, {"trains": True, "allow": False, "verified": True}),
    ("openrouter.ai", lambda model: model.endswith(":free"), {"trains": True, "allow": False, "verified": True}),
    ("integrate.api.nvidia.com", lambda model: True, {"trains": False, "allow": False, "verified": False}),
    ("api.cerebras.ai", lambda model: True, {"trains": False, "allow": False, "verified": False}),
    ("api.groq.com", lambda model: True, {"trains": False, "allow": True, "verified": True}),
]


class ProviderConfig(BaseModel):
    name: str
    kind: Literal["openai_compat", "anthropic", "claude_cli"] = "openai_compat"
    base_url: str = ""
    model: str
    enabled: bool = True
    supports_tools: bool = True
    requires_key: bool = True
    trains_on_data: bool = False
    allow_sensitive: bool = True
    tier: Literal["fast", "deep"] = "fast"
    key_name: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)
    timeout_s: float = Field(30.0, gt=0, le=900)
    limits: Limits = Field(default_factory=Limits)
    small_model: str = ""  # a cheaper model on the same key for simple turns (own free quota); empty: none
    # True: you checked this provider's data terms yourself; False: known to be unverified; None: not known
    policy_verified: bool | None = None
    # a paid provider answers only the capabilities listed here, while this month's spend is under the cap
    paid: bool = False
    monthly_cap_usd: float = Field(0.0, ge=0)
    price_per_mtok_usd: float = Field(0.0, ge=0)  # blended price per million tokens, to estimate the spend
    capabilities: list[str] = Field(default_factory=list)  # e.g. ["coding", "vision"]

    def is_paid(self) -> bool:
        return self.paid or self.kind == "anthropic"

    # takes reasoning_effort / max_completion_tokens / include_reasoning (frugal mode); None: known models only
    supports_reasoning_effort: bool | None = None
    # accepts stream_options.include_usage (token receipts); None: known providers only (others may reject it)
    stream_usage: bool | None = None

    def effective_stream_usage(self) -> bool:
        if self.stream_usage is not None:
            return self.stream_usage
        return any(host in self.base_url for host in ("api.groq.com", "openrouter.ai"))

    def effective_reasoning_support(self) -> bool:
        if self.supports_reasoning_effort is not None:
            return self.supports_reasoning_effort
        return "api.groq.com" in self.base_url and "gpt-oss" in self.model

    def effective_policy(self) -> dict:
        """What this provider does with prompts, with the verified facts for known hosts applied to older
        configs: {"trains_on_data", "allow_sensitive", "verified"}."""
        trains, allow, verified = self.trains_on_data, self.allow_sensitive, self.policy_verified
        if verified is not True:
            for host, model_matches, known in KNOWN_POLICY:
                if host in self.base_url and model_matches(self.model):
                    trains = trains or known["trains"]
                    allow = allow and known["allow"]
                    verified = known["verified"] if verified is None else verified
                    break
        if verified is False:
            allow = False
        return {"trains_on_data": trains, "allow_sensitive": allow, "verified": verified}

    def privacy_label(self) -> str:
        p = self.effective_policy()
        use = ("may train on prompts" if p["trains_on_data"] else "data use not verified" if p["verified"] is False
               else "data use unknown" if p["verified"] is None else "does not train on prompts")
        return f"{use}; {'private turns allowed' if p['allow_sensitive'] else 'not for private turns'}"

    def effective_small_model(self) -> str:
        if self.small_model:
            return self.small_model
        if "api.groq.com" in self.base_url and self.model == "openai/gpt-oss-120b":
            return "openai/gpt-oss-20b"
        return ""

    def effective_limits(self) -> Limits:
        """The configured limits, or the verified ones for this host and model when none are configured."""
        if any(v is not None for v in self.limits.model_dump().values()):
            return self.limits
        for host, model_matches, known in KNOWN_LIMITS:
            if host in self.base_url and model_matches(self.model):
                return known
        return self.limits

    @field_validator("extra")
    @classmethod
    def _extra_is_plain_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        _check_extra(value, "")
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as e:
            raise ValueError(f"extra must be plain JSON: {e}") from e
        return value


def _lang_code(v: str) -> str:
    v = v.strip().lower()
    if v and not re.fullmatch(r"[a-z]{2,3}", v):
        raise ValueError("language must be an ISO 639-1 code like 'en', or empty for auto-detect")
    return v


class SttConfig(BaseModel):
    provider: Literal["groq", "local"] = "groq"
    groq_model: str = "whisper-large-v3-turbo"
    local_model: str = "base.en"
    auto_model: bool = False  # choose the local recogniser's size from this computer (memory, processor, GPU) instead of local_model
    local_only: bool = False
    # noise guards: Whisper's own speech detector and confidence cut-offs, so a fan or a cough is not "words"
    vad_filter: bool = True
    no_speech_threshold: float = Field(0.6, ge=0.0, le=1.0)
    log_prob_threshold: float = Field(-1.0, le=0.0)
    compression_ratio_threshold: float = Field(2.4, gt=0.0)
    hotwords: str = "Minty"  # names worth biasing towards (yours, playlists); comma-separated
    # ISO 639-1 code sent to the recogniser; "" = let it guess. A guess outside allowed_languages is retried once.
    language: str = "en"
    allowed_languages: list[str] = ["en"]

    @field_validator("language")
    @classmethod
    def _lang(cls, v: str) -> str:
        return _lang_code(v)

    @field_validator("allowed_languages")
    @classmethod
    def _langs(cls, v: list[str]) -> list[str]:
        return [_lang_code(x) for x in v if x.strip()]


class TtsConfig(BaseModel):
    # kokoro sounds natural; piper is lighter (and the fallback); fish = Fish Audio cloud voices (key "fish"),
    # with the local voice whenever Fish is unreachable, out of credit, or private mode is on
    engine: Literal["kokoro", "piper", "fish"] = "kokoro"
    fish_voice: str = ""  # voice id from fish.audio (the part after /m/ in a voice's link); empty = Fish default
    fish_model: str = "s2.1-pro"
    fish_latency: Literal["low", "balanced", "normal"] = "balanced"
    parallel: int = Field(1, ge=1, le=3)  # sentences made into speech at the same time; 1 = one after the other. More is sooner, and uses more of the processor
    kokoro_voice: str = "am_michael"
    kokoro_model: str = ""  # empty: <data dir>/minty/voices/kokoro-v1.0.onnx
    kokoro_voices: str = ""  # empty: <data dir>/minty/voices/voices-v1.0.bin
    speed: float = Field(1.0, ge=0.5, le=2.0)
    voice_model: str = ""  # Piper .onnx
    minty_filter: bool = False


class AudioConfig(BaseModel):
    listening_setup: Literal["unknown", "headphones", "speakers"] = "unknown"  # set by the first-run wizard
    input_device: str | None = None
    output_device: str | None = None
    wake_mode: Literal["words", "model"] = "words"  # words: say the name; model: openWakeWord
    wake_phrases: list[str] = ["minty", "mintee", "minti", "mindy"]
    wake_word: str = "hey_jarvis"  # openWakeWord model used when wake_mode = "model"
    wake_threshold: float = Field(0.5, ge=0.0, le=1.0)
    speech_rms_threshold: int = Field(400, ge=0)
    auto_sensitivity: bool = True  # the speech bar follows the room and the microphone (audio/sensitivity.py); off: speech_rms_threshold is fixed
    mic_sensitivity: float = Field(1.0, ge=0.25, le=8.0)  # above 1: hears quieter speech (a deaf mic); below 1: needs louder (a hot mic)
    listen_duck: bool = True  # turn everything else that is playing way down while it listens, and back up afterwards
    listen_duck_level: float = Field(0.1, ge=0.0, le=0.9)  # what it is turned down to (0.1 = a tenth of the volume it had)
    silence_ms: int = Field(900, ge=80)
    # Hear the turn during the pause: after this much silence the words so far are recognised in the background, so they are ready
    # when the pause ends (or, with early_finish_ms, a plainly finished sentence ends the turn sooner). Off: recognise at the end.
    speculative_stt: bool = True
    spec_after_ms: int = Field(350, ge=160, le=2000)
    early_finish_ms: int = Field(600, ge=0, le=3000)  # a sentence the recogniser ended with . ? ! ends the turn after this much silence; 0 = never
    max_wait_ms: int = Field(4000, ge=80)
    min_speech_frames: int = Field(3, ge=1, le=25)  # 80 ms frames of sound before it counts as speech (blips ignored)
    noise_floor_factor: float = Field(2.5, ge=0.0, le=20.0)  # speech must be this much louder than the room; 0 = off
    min_voiced_ms: int = Field(250, ge=0, le=3000)  # a recording with less real speech than this is ignored as noise
    vad_assist: bool = True  # a speech detector lets quiet speech count even below speech_rms_threshold
    max_record_s: int = Field(15, ge=1, le=120)
    barge_in: bool = True
    voice_mode: Literal["live", "classic"] = "live"  # live: Gemini speech-to-speech; classic: STT+LLM+TTS
    live_model: str = ""  # empty: pick the newest Live API model this key can use
    live_voice: str = "Charon"
    live_idle_s: int = Field(20, ge=5, le=600)  # end a live conversation after this much quiet
    live_echo_guard: bool = True  # mute the mic while Gemini speaks (turn off with headphones to talk over it)
    live_barge_in: bool = False  # with the guard on, let a voice clearly louder than the echo through, so you can interrupt Gemini
    live_barge_in_rms: int = Field(3000, ge=200, le=20000)  # how loud (RMS of a 16-bit frame) that must be; raise it if the speakers set it off
    music_aware: bool = True  # while media plays: stricter wake matching, short utterances only
    music_pause: bool = True  # pause media during a conversation and resume it after
    live_captions: bool = True  # show words as you speak (extra local transcription)
    caption_interval_ms: int = Field(600, ge=200, le=3000)  # how often chunked captions refresh
    # "sherpa": a streaming recogniser (pip install sherpa-onnx + a model folder) gives true word-by-word text
    streaming_asr: Literal["off", "sherpa"] = "off"
    streaming_model_dir: str = ""  # empty: <data dir>/minty/voices/sherpa-streaming
    follow_up: bool = True  # keep listening briefly after a reply, no wake word needed
    wake_ack: str = "Yes, sir?"  # said when you say only "Minty", so you know it is listening; "" = silent
    earcons: Literal["off", "chime"] = "off"  # chime: a short tone instead of speaking wake_ack
    smart_turn: bool = False  # wait a moment when a sentence sounds unfinished ("and", "because", "um")
    turn_hold_ms: int = Field(1200, ge=300, le=4000)
    # a small fast model reads the transcript after a pause and says finished or still going (needs smart_turn)
    turn_judge: bool = False
    turn_judge_hold_ms: int = Field(2500, ge=500, le=6000)  # how long to wait for more when it says "still going"
    turn_judge_timeout_ms: int = Field(800, ge=200, le=3000)  # no answer in this time: answer as usual
    turn_judge_provider: str = ""  # empty: the first fast provider that is not a Claude process
    push_to_talk: bool = False  # opt-in: hold a key to talk; no wake word while on
    ptt_key: str = "f9"  # the key pynput listens for while push_to_talk is on
    aec: bool = False  # PipeWire echo cancellation (set up with `minty aec on`); speakers only
    hands_free_stop: bool = False  # with aec: "stop" / "wait" while it speaks halts it without the wake word


class PersonalityConfig(BaseModel):
    active: str = "minty"
    formality: int = Field(5, ge=0, le=10)
    humor: int = Field(3, ge=0, le=10)
    verbosity: int = Field(3, ge=0, le=10)
    service_rules: bool = True  # the ego-free service paragraph (personality.SERVICE_RULES) in every personality
    prompt_library: bool = True  # the Wave 16 prompt modules (promptlib.py); each is used only after it passed its A/B gate
    prompt_modules_off: list[str] = Field(default_factory=list)  # module names to leave out, e.g. ["wellbeing"]


class MemoryConfig(BaseModel):
    window_min: int = Field(30, ge=1, le=1440)  # conversation kept word for word in context
    summary_days: int = Field(7, ge=0, le=60)  # daily summaries included in the prompt
    # newest characters of "earlier today, word for word" in the prompt; 5000 was the original size, and from midday
    # it was a third of every request (2026-10-11 trace). The gist index finds the rest by words.
    earlier_chars: int = Field(2000, ge=200, le=20000)
    auto_remember: bool = True  # save lasting personal facts he mentions (sizes, dates, people) without being asked
    audit: bool = True  # the memory list with sources, correction and forgetting (read-only until you act)
    contradictions: bool = True  # a new fact that disagrees with an old one is asked about, never overwritten
    weekly_learned: bool = False  # on Sundays: what I learned about you this week, to review
    compress: bool = True  # compress finished days and months (raw transcripts are always kept)
    gist_index: bool = True  # older memory is found by words, a few lines per request, instead of pasted in
    gist_budget_tokens: int = Field(250, ge=50, le=1500)  # most the recalled lines may cost in a prompt
    # background model work (day summaries and what was learned) is one call per day, at night or when overdue,
    # and only while at least compact_min_share of the day's known free capacity is left
    batch_reflection: bool = True
    compact_from_hour: int = Field(1, ge=0, le=23)
    compact_to_hour: int = Field(6, ge=1, le=24)
    compact_min_share: float = Field(0.5, ge=0, le=1)


class DebugConfig(BaseModel):
    """Test and diagnosis switches. All off by default."""
    inject: dict[str, float] = Field(default_factory=dict)  # failure kind -> share of provider calls (0..1)
    only: list[str] = Field(default_factory=list)  # limit injection to these providers (empty: all)
    seed: int | None = None  # a fixed seed makes an injected run repeatable
    slow_s: float = Field(0.0, ge=0, le=60)  # wait this long before an injected failure (a slow provider)


class OpsConfig(BaseModel):
    """Running 24/7: maintenance, heartbeat, quiet hours."""
    # start in safe mode (basic plugins only) after three starts without a clean stop in ten minutes; off, because
    # closing the window is a normal way to stop. Ask for safe mode yourself with `minty --safe`.
    auto_safe_mode: bool = False
    maintenance: bool = True  # one housekeeping pass a day in a quiet window
    maintenance_time: str = "03:30"
    disk_free_floor_mb: int = Field(1024, ge=64)  # below this much free space, old traces and caches are trimmed
    disk_max_config_mb: int = Field(2048, ge=64)  # ... and above this much in the config folder
    quiet_voice_from: str = "20:30"  # speech is quieter from this time ...
    quiet_voice_to: str = "06:00"  # ... until this one
    quiet_voice_gain: float = Field(0.5, ge=0.05, le=1.0)  # 1.0 = no change
    night_wake_boost: float = Field(0.15, ge=0.0, le=0.4)  # added to the model wake threshold in those hours
    day_start: bool = True  # the first conversation of the day is followed by a short brief (if none was given yet)
    day_start_after: str = "04:00"
    heartbeat_time: str = ""  # HH:MM: a daily "I'm alive" note at this time; empty = off (the file is always kept)
    backup: bool = True  # an encrypted backup of memory, notes and settings in the nightly window
    backup_folder: str = ""  # empty: <config folder>/backups (point it at a USB stick or another disk to be safer)
    backup_include_raw: bool = False  # also the word-for-word transcripts (they are private and can be large)


class TelegramConfig(BaseModel):
    """Your phone through your own bot (one bot per machine; the token is in the key store as 'telegram')."""
    enabled: bool = False
    allowed_ids: list[int] = Field(default_factory=list)  # numeric chat ids that were paired; nobody else is answered
    machine_name: str = ""  # shown on every reply and in the traces; empty: this computer's host name
    class3_passphrase: bool = False  # option 551: allow class-3 approvals from Telegram with a passphrase (off)
    typing: bool = True  # "typing..." and progress edits while a request runs
    stream: bool = True  # long answers appear as one message that is edited
    commands: bool = True  # /note /todo /remind /idea /where /jobs /stop /status
    briefs: bool = True  # send the morning/evening brief as a chat message
    relay: bool = False  # file relay to another machine's bot (556); must be on at both ends
    relay_peers: dict[str, int] = Field(default_factory=dict)  # machine name -> that bot's numeric id
    screenshots: bool = False  # send browser screenshots to your chat (checked on the laptop first; off by default)
    screenshots_unchecked: bool = False  # send them even when no text reader (tesseract) can check them first
    voice_replies: Literal["auto", "on", "off"] = "auto"  # auto: a voice note gets a voice answer; /voice sets it per chat
    voice_max_s: int = Field(180, ge=10, le=900)  # longest voice note taken (the local model is slow on this laptop)


class HealthConfig(BaseModel):
    """Your training, food, sleep and body data in one local store (nothing is collected until you agree per domain)."""
    hub: bool = True
    hevy: bool = False  # poll Hevy for new workouts (read-only; needs a Hevy Pro API key: minty set-key hevy)
    hevy_poll_hours: int = Field(6, ge=1, le=48)
    hc_bridge: bool = True  # import exports (Hevy, Cronometer, Strava archive, Health Connect) sent to the Telegram bot
    strava_api: bool = False  # not built: the archive and Health Connect routes are used (Strava's API terms)


class CoachConfig(BaseModel):
    """The fitness and nutrition coach: off until you switch it on (and agree to the training domain)."""
    enabled: bool = False
    nutrition: bool = True  # protein, maintenance estimate, water, sleep (when the coach is on)
    briefs: bool = False  # a pre-workout brief at pre_workout_time and a debrief when a workout syncs, to Telegram
    pre_workout_time: str = ""  # HH:MM; empty = only when asked
    # no weight-loss, deficit, fasting, BMI or weigh-in coaching for under 18 or unknown age. Off by default because
    # you asked not to have extra rules for your age; the red-flag stop and extreme-target floors never switch off.
    age_rules: bool = False
    planning: bool = True  # programme builder, deload planner, PR board, streaks, goal feasibility, rescue (when coach is on)
    air_quality: bool = False  # an air-quality note before outdoor training (Open-Meteo; sends your coordinates)
    latitude: float | None = None
    longitude: float | None = None


class FinanceConfig(BaseModel):
    """A private, read-only money tracker (nothing here can pay or transfer). Off until you switch it on."""
    enabled: bool = False
    default_account: str = "Cash"  # where a quick add and a forwarded SMS go when no account is named
    bill_reminders: bool = True  # a notice when a bill you set is due within three days and not yet paid
    insights: bool = True  # budget alerts, subscription audit, forecast, safe to spend, scam check (when enabled)


class LifeConfig(BaseModel):
    """Dashboards, the briefing, the weekly review and goals (read from what the coach and the ledger hold)."""
    review: bool = True  # the tools: three views, 60-second briefing, weekly review, goals
    checkins: bool = False  # a two-question check-in in Telegram at checkin_time
    checkin_time: str = "20:00"
    review_push: bool = False  # the weekly review to Telegram on Sundays at review_time
    review_time: str = "18:00"


class VoiceConfig(BaseModel):
    """What the voice does beyond hearing and speaking: each item is its own switch."""
    corrections: bool = True  # "no, I said ..." asks the corrected request once
    vocabulary: bool = False  # the words you corrected are passed to the recogniser as hints
    playback_controls: bool = True  # "go on", "skip", "repeat that", "shorter"
    resume_after_reflex: bool = False  # a quick command (volume, mute, pause) said over an answer is done, then the answer carries on; judge by ear
    dictation: bool = True  # "start dictation" ... "stop dictation": everything between is a note
    notes: bool = True  # "note for me": listen without the wake word and write every thing down until "Minty, stop noting"
    notes_hud_lines: int = Field(6, ge=1, le=15)  # how many of the latest notes the HUD lists while noting
    pronunciations: bool = True  # your pronunciation dictionary is applied just before speaking
    whisper: bool = False  # speak quietly
    ambient_volume: bool = False  # louder in a loud room, quieter in a quiet one
    pace_matching: bool = False  # follow how fast you talk
    time_of_day: bool = False  # a calmer, shorter voice in the evening (profiles below)
    duck_music: bool = False  # lower other audio while Minty speaks instead of pausing it
    progress_narration: bool = False  # a short spoken update while a long job runs
    bilingual: bool = False  # understand and answer in Hindi when you switch
    speaker_check: bool = False  # a convenience check before sensitive actions; never the only check
    time_profiles: list[dict] = Field(default_factory=lambda: [{"from": "21:30", "to": "06:30", "gain": 0.85,
                                                                 "speed": 0.95}])  # used when time_of_day is on


class OverlayConfig(BaseModel):
    """The small on-screen HUD (a separate process: minty overlay). Off by default; the terminal stays the default."""
    hud: bool = False  # show state, captions and timers while another window is active
    timer: bool = False  # kept for timers that open their own countdown (the HUD lists them already)
    corner: Literal["bottom-right", "bottom-left", "top-right", "top-left"] = "bottom-right"
    opacity: float = Field(0.92, ge=0.3, le=1.0)
    hints: list[str] = ["minty"]  # a window whose title has one of these counts as Minty being focused
    scenes: dict[str, list[str]] = Field(default_factory=dict)  # name -> ["https://...", "app:org.gnome.Calculator"]
    distraction_words: list[str] = ["youtube", "instagram", "reddit", "twitter", "netflix", "facebook", "tiktok"]


class VaultConfig(BaseModel):
    """The notes folder Minty owns: plain Markdown, a git history, a rebuildable index. Off by default."""
    enabled: bool = False
    path: str = ""  # empty: ~/.minty/brain
    git: bool = True  # one commit per change (needs git installed; without it changes are logged instead)
    proposals: bool = True  # each evening the inbox gets suggestions (folder, tags, project) you approve
    proposals_time: str = "21:30"
    stale_months: int = Field(18, ge=3, le=120)  # a source or a time-sensitive note older than this is flagged
    resurface: bool = False  # "on this day" and review-due notes join the morning brief
    glossary_hints: bool = True  # your glossary words are passed to the speech recogniser as hints
    hybrid: bool = False  # meaning-based search; refuses to turn on until the evaluation gate has passed
    encryption: bool = False  # the encrypted private area (unlocked only at the terminal or in the panel)
    private_lock_minutes: int = Field(10, ge=1, le=240)  # idle time before it locks itself


class ButlerConfig(BaseModel):
    """The personal-assistant domains (chores, follow-ups, where things are, documents). Off until switched on."""
    enabled: bool = False
    domains: list[str] = ["chores", "tasks", "waiting", "place", "leaving", "documents",
                          "ideas", "competitions"]  # which ones answer
    reminders: bool = True  # spoken or pushed when something is due (urgency rules decide how)
    # limited, expiring pre-approvals YOU make with `minty butler standing grant`; never class 3, never tainted
    standing_approvals: bool = False
    urgency: bool = True  # only what is time-sensitive interrupts; the rest joins a digest
    max_interrupts_per_day: int = Field(3, ge=0, le=50)
    digest_times: list[str] = ["08:30", "18:00"]
    checkin: bool = False  # the check-in timer: opt in, then "check in with me in two hours"
    checkin_contact_chat: int = 0  # Telegram chat id of a person who started the bot (notify-only, no control)
    checkin_contact_name: str = ""
    checkin_user_name: str = ""  # how the contact is told who is checking in
    checkin_grace_min: int = Field(10, ge=1, le=120)
    photo_filing: bool = False  # a photo or PDF captioned "file this" becomes an untrusted note in sources/
    mailbox_triage: bool = False  # mail_triage sorts the dedicated mailbox into receipts, bills, alerts, to answer


class OfflineConfig(BaseModel):
    enabled: bool = True  # answer what needs no model while the internet is down (off: say the models are down)


class ProfileConfig(BaseModel):
    """The 500-question profile (profile.py). Your answers stay on this computer; related ones go to the model with a request."""
    enabled: bool = True  # add your related answers to what the model sees (off: the answers are only stored)
    max_chars: int = Field(1800, ge=200, le=8000)  # the most characters of them added to one request


class InstantConfig(BaseModel):
    """Questions the computer can answer by itself (docs/instant-answers.md): no model call, so they come back at once."""
    enabled: bool = True
    actions: bool = True  # set a timer, volume, pause the music: one tool call, no model
    fuzzy: bool = True  # a command with a spelling slip ("turn the volumn down") is still understood, without a model
    skip: list[str] = []  # categories to leave to the model, for example ["fun and games"]


class UpdateConfig(BaseModel):
    """Updates from the project's GitHub repository (selfupdate.py). Checking is on; installing by itself is off."""
    repo: str = ""  # "owner/name" of the repository on GitHub, filled in when the project is published
    check: bool = True  # ask GitHub once a day whether there is a newer release, and say so (nothing is installed)
    check_every_hours: int = Field(24, ge=1, le=168)
    auto_install: bool = False  # install a newer release by itself: only for a repository whose owner you trust completely
    verify_signed: bool = False  # only install a release whose git tag is signed by a key your git knows

    @field_validator("repo")
    @classmethod
    def _owner_slash_name(cls, value: str) -> str:
        if value and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
            raise ValueError('use "owner/name", for example "someone/minty"')
        return value


class LocalLlmConfig(BaseModel):
    """An optional small local model for titles, intents and summaries (item 531). Off, and gated by a measurement."""
    enabled: bool = False
    model_path: str = ""  # a .gguf file; Minty never downloads one for you
    n_ctx: int = Field(2048, ge=256, le=8192)
    min_tokens_per_s: float = Field(4.0, gt=0)
    max_ram_mb: int = Field(2500, ge=256)


class JobsConfig(BaseModel):
    backend: Literal["jsonl", "sqlite"] = "sqlite"  # jsonl = the original append-only file (kept one release)
    max_attempts: int = Field(1, ge=1, le=5)  # runs of a job's work after an error; 1 = never retried (the old way)
    retry_delay_s: float = Field(5.0, ge=0, le=300)  # first back-off, doubled each retry (at most 60 s)
    resume: bool = True  # an interrupted job that saved a checkpoint can be picked up again by voice


class SafetyConfig(BaseModel):
    confirm_risky: bool = True  # false: never ask below class 3 (deny rules still apply; class 3 is always asked)
    # careful: ask before any change; commits: ask before anything others see or that is hard to undo;
    # trusted: ask only for money, credentials and deletion
    mode: Literal["careful", "commits", "trusted"] = "commits"
    # ordered; first match wins: {"match": "tool_name_glob:argument_glob", "decision": "allow|ask|deny"}
    rules: list[dict[str, str]] = Field(default_factory=list)
    audit: bool = True
    approval_inbox: bool = True  # every question is also kept as an approval record bound to its action
    # runaway loops: changes (risk class 2 and up) per tool per rolling hour, a named limit per tool or pattern
    # (0 = none), and a ceiling on all changes made on its own in an hour
    default_hourly_limit_changes: int = Field(40, ge=0, le=10_000)
    tool_hourly_limits: dict[str, int] = Field(default_factory=dict)
    autonomy_budget_per_hour: int = Field(120, ge=0, le=100_000)
    privacy_mode: bool = False  # nothing leaves except to providers that don't train on it; network tools refused
    audit_installs: bool = True  # the install question says what OSV.dev knows about the package
    weekly_review: bool = True  # a short note on screen every Sunday: what could change things and was never used
    lockdown_phrase: str = "lockdown"  # said by voice or typed, it stops everything (another phrase works too)
    unlock_phrase: str = "end lockdown"  # only the voice pipeline, the terminal or the API token can lift it
    block_secret_egress: bool = True  # a tool call whose arguments hold a stored key or a planted fake key is stopped
    mandates: bool = True  # what you just asked for is not asked about again (class 3 and untrusted content excepted; policy.Mandate)
    # off unless you turn it on: "email sam@example.com that ..." is sent without a question when the address is in your own words, the mail is one
    # short plain note (no link, no other address, no sensitive word), through Composio, at most five a day. Everything else that reaches a person is asked.
    mandate_send_mail: bool = False
    honeytokens: bool = True  # plant a decoy key file in the workspace; seeing its keys go out is an alarm
    mask_pii_in_traces: bool = True  # e-mail, phone, card, Aadhaar and PAN numbers are masked in the traces
    # the same send, download or install repeated inside this many seconds runs once (0 = off, the old way)
    idempotency_window_s: float = Field(120.0, ge=0, le=3600)
    # names never sent to a provider that may train on prompts (they become PERSON_1, ...); yours, family, friends
    pseudonym_names: list[str] = Field(default_factory=list)


class StudyConfig(BaseModel):
    """The study suite: planning, flashcards, checking, practice. Off until switched on."""
    enabled: bool = False
    window_start: str = "16:30"  # study time on weekdays
    window_end: str = "20:30"
    weekend_start: str = "10:00"
    weekend_end: str = "18:00"
    block_min: int = Field(40, ge=10, le=120)
    break_min: int = Field(10, ge=0, le=60)
    max_minutes_per_day: int = Field(150, ge=30, le=480)
    busy: dict[str, list[str]] = Field(default_factory=dict)  # "tue": ["17:00-18:00"] (tuition, sport)
    timetable: dict[str, list[dict]] = Field(default_factory=dict)  # "mon": [{"start","end","subject","bring"}]
    class_reminders: bool = False  # "Maths in 15 minutes. Bring: geometry box" (a reminder you asked for)
    lead_min: int = Field(15, ge=5, le=60)
    new_cards_per_day: int = Field(10, ge=1, le=100)
    weekly_review: bool = False  # Sunday evening: what got done and what is next

    @field_validator("window_start", "window_end", "weekend_start", "weekend_end")
    @classmethod
    def _hhmm(cls, value: str) -> str:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("use 24-hour HH:MM, for example 16:30")
        return value


class ProductivityConfig(BaseModel):
    """Habits, people, trips, meals, alarms and the day's plan. Off until switched on; every push is its own switch."""
    enabled: bool = False
    plan_push: bool = False  # the day's plan at plan_time
    plan_time: str = "07:30"
    wind_down: bool = False  # a short routine at wind_down_time
    wind_down_time: str = "21:30"
    wind_down_steps: list[str] = ["Phone on charge", "Pack tomorrow's bag", "Lights out"]
    summary_push: bool = False  # the end-of-day summary at summary_time
    summary_time: str = "20:30"
    alarm_brief: bool = True  # a short spoken brief after the alarm chimes
    snooze_min: int = Field(8, ge=1, le=60)

    @field_validator("plan_time", "wind_down_time", "summary_time")
    @classmethod
    def _hhmm(cls, value: str) -> str:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("use 24-hour HH:MM, for example 07:30")
        return value


class RecorderConfig(BaseModel):
    """The lesson / meeting recorder. Off by default; it only records between your explicit start and stop."""
    enabled: bool = False
    keep_audio: bool = False  # off: the audio is deleted as soon as the text is made
    max_minutes: int = Field(180, ge=5, le=480)
    folder: str = ""  # empty: a recordings folder next to your settings


class WebConfig(BaseModel):
    """Browser and web helpers (WP-16). Each part has its own switch and everything is off until you turn it on."""
    watch: bool = False  # price, stock and page watchers and the wishlist
    min_watch_hours: float = Field(6.0, ge=1.0, le=168.0)  # no watch is ever checked more often than this
    downloads: bool = False  # browser downloads go to a quarantine folder, are hashed and (with a key) looked up
    release_folder: str = "~/Downloads"  # where an approved download is released to
    helpers: bool = False  # read-later, safe-open, "show me" pages, captions, sessions, form log, search fallback
    show_open: bool = False  # open a "show me" page in your browser as soon as it is made
    folder: str = ""  # quarantine and "show me" pages: empty means next to your settings


class SystemConfig(BaseModel):
    """Linux operations (WP-17). Everything is off until switched on; the tools that change the machine have their own switch."""
    enabled: bool = False  # read-only help: what is this process / file, updates, crashes, services, scripts
    controls: bool = False  # change things: volume, outputs, brightness, night light, power profile, lock, sleep, USB
    alerts: bool = False  # battery, heat and CPU-hog alerts (with hysteresis and a rate limit)
    resource_gate: bool = False  # heavy background work waits while the computer is busy, short of memory or hot
    low_battery: int = Field(20, ge=5, le=50)
    hog_percent: int = Field(85, ge=50, le=100)
    notifications: bool = False  # the notification reader (memory only; speaks when asked)
    notification_apps: list[str] = []  # opt-in, per app: part of the app name, e.g. "telegram"


class DevConfig(BaseModel):
    """Developer helpers (WP-18). Off until switched on; each part that sends something out has its own switch."""
    enabled: bool = False  # repo summaries, git digest, test runs (asks), command explainer, snippets, plugin kit, trends
    root: str = "~/Documents/project"  # git folders directly under it are your projects
    projects: dict[str, str] = {}  # extra projects by name -> folder
    test_timeout_s: int = Field(300, ge=30, le=1800)
    osv: bool = False  # dependency radar: sends package names and versions (nothing else) to OSV.dev
    review: bool = False  # branch review through `claude -p` (uses your Claude plan; asks first)
    ci_watch: bool = False  # tell me when CI on a watched project goes red or green (needs the GitHub CLI)
    ci_minutes: int = Field(10, ge=5, le=240)  # never asks GitHub more often than this
    ci_projects: list[str] = []


class CreateConfig(BaseModel):
    """Research, creation and media helpers (WP-19). Off until switched on; what sends something out has its own switch."""
    enabled: bool = False  # news brief, paper watcher, idea review, mind maps, slides, translation glossary, claim evidence, media
    feeds: list[str] = []  # news feed addresses (more can be added by voice)
    news_push: bool = False  # the news brief once a day at news_time
    news_time: str = "07:45"
    paper_push: bool = False  # tell me about new arXiv papers on the topics I watch
    images: bool = False  # free image generation through your own Cloudflare account (sends only the prompt)
    daily_images: int = Field(8, ge=1, le=30)
    folder: str = "~/Minty-Workspace/created"  # everything made lands here, as new files
    media_folders: list[str] = ["~/Minty-Workspace/media", "~/Music", "~/Videos", "~/Downloads"]  # media inputs allowed

    @field_validator("news_time")
    @classmethod
    def _hhmm(cls, value: str) -> str:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("use HH:MM")
        return value


class SelfcheckConfig(BaseModel):
    """The weekly self-report, the daily tip and card (WP-21). Off until switched on; each push has its own switch."""
    enabled: bool = False
    weekly_report: bool = False  # say the self-report once a week (Sunday evening)
    tip_push: bool = False  # one tip a day at tip_time
    tip_time: str = "08:30"
    card_push: bool = False  # send the daily summary card to the phone (Telegram) at card_time
    card_time: str = "21:00"

    @field_validator("tip_time", "card_time")
    @classmethod
    def _hhmm(cls, value: str) -> str:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("use HH:MM")
        return value


class McpConfig(BaseModel):
    """MCP servers (WP-20). Off until switched on. Servers and tools are added and allowed at the terminal only."""
    enabled: bool = False
    timeout_s: int = Field(30, ge=5, le=120)


class ShellConfig(BaseModel):
    """The terminal (shellkit.py, plugin `shell`). On by default, but a command can do anything you could do yourself, so only a few
    read-only commands run unasked and every other one is asked each time, with the exact command read out."""
    enabled: bool = True  # on by default: only a few read-only commands run unasked; every other command is asked each time
    timeout_s: int = Field(30, ge=1, le=300)  # the longest a command may run; one asked for less is stopped sooner
    max_output_chars: int = Field(8000, ge=500, le=50000)  # a longer output keeps its start and its end
    workdir: str = ""  # where commands start; empty means the workspace folder (~/Minty-Workspace)


class IntegrationsConfig(BaseModel):
    """Home, calendar and external data (WP-20). Off until switched on; each part has its own switch."""
    enabled: bool = False
    calendar: bool = False  # read-only ICS feeds
    home: bool = False  # scenes by name and the energy summary through Home Assistant (uses the smart_home plugin's address and token)
    tariff: float = Field(0.0, ge=0.0, le=1000.0)  # price per kWh for the energy summary (0 = do not show a cost)
    sleep_timer: bool = False  # fade the volume and pause after a time
    weather_alerts: bool = False  # forecast-derived alerts for `location`, said once each
    location: str = ""  # a city name, for the weather alerts
    lan: bool = False  # the local network inventory, on request only
    lan_ranges: list[str] = []  # private ranges the scan may look at, e.g. 192.168.1.0/24
    timezone: str = "Asia/Kolkata"  # how calendar times are shown


class GmailWatchConfig(BaseModel):
    """Notices what matters in your Gmail through the Composio server you set up, and tells you. Off until you turn it on: it reads your mail
    (sender, subject and a short snippet; never the whole message), only reads, and polls with a growing wait if anything fails."""
    enabled: bool = False
    server: str = "composio"  # the MCP server (minty mcp composio / add-remote) that has your Gmail connected
    accounts: list[str] = Field(default_factory=list)  # aliases of the Gmail accounts to watch; empty: every connected one
    interval_s: float = Field(600.0, ge=300.0, le=86400.0)
    vips: list[str] = Field(default_factory=list)  # addresses or @domains that always matter
    topics: list[str] = Field(default_factory=list)  # words and phrases about what you are working on
    max_per_poll: int = Field(20, ge=1, le=100)
    min_confidence: float = Field(0.5, ge=0.0, le=1.0)  # below this, "important" is only "notable"
    lookup_senders: bool = True  # one search per new sender, to know whether you have written to them before
    reminders: bool = True  # an important mail with a deadline a few days off gets one reminder the morning before


class NotifyConfig(BaseModel):
    """Who is told what, when: the one layer every proactive notice goes through (severity, no repeats, quiet hours, retries)."""
    phone: bool = True  # important and urgent notices also go to your paired Telegram chat (the title only for private ones)
    urgent_bypass_quiet: bool = False  # only an urgent notice may break the quiet hours, and only if this is on
    max_important_per_hour: int = Field(4, ge=1, le=60)
    max_urgent_per_hour: int = Field(10, ge=1, le=60)
    remind_after_h: float = Field(4.0, ge=0.5, le=48.0)  # an urgent notice nobody dealt with is said again after this long
    max_reminders: int = Field(2, ge=0, le=10)


class ProactiveConfig(BaseModel):
    """What Minty may start by itself. Everything has an off switch."""
    enabled: bool = True
    brief: bool = True  # a short morning brief
    brief_time: str = "07:00"
    due_nudges: bool = True  # homework and notices that are due
    attention: bool = True  # things that need you (a blocked mission, an e-mail that may not have gone, an overdue follow-up)
    night_from: str = "22:00"  # nothing spoken on its own between these (held, shown on the display)
    night_to: str = "07:00"
    max_per_hour: int = Field(4, ge=1, le=60)

    @field_validator("brief_time", "night_from", "night_to")
    @classmethod
    def _hhmm(cls, value: str) -> str:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("use 24-hour HH:MM, for example 07:00")
        return value


class FrugalConfig(BaseModel):
    """Token-saving profiles (frugal.py). F0 = off until the A/B evaluation shows F1 keeps answers as good."""
    level: Literal["F0", "F1"] = "F0"
    auto_in_brownout: bool = True  # F1 by itself while little of today's free capacity is left


class RouterConfig(BaseModel):
    """How model calls are routed across providers. Each switch keeps the old behaviour one flip away."""
    # simple spoken turns ask reasoning models (Groq's gpt-oss) for this effort: faster to the first word, slightly less careful.
    # Hard turns, thinking mode and other providers are untouched. Off by default until you have heard the difference.
    fast_reasoning: Literal["", "low", "medium"] = ""
    warm_on_wake: bool = False  # open the connection to the model provider the moment the name is heard, so the first request does not wait for it
    classify_errors: bool = True  # request-shaped errors (too large, tool not offered) never cool a provider
    recover_request_errors: bool = True  # the agent repairs such a request (load the toolkit / trim) and retries once
    health_order: bool = True  # recent success decides the order; providers with no recent success are demoted
    allow_deep_fallback: bool = False  # deep (slow, thinking) models may answer fast turns when the fast ones fail
    persist_state: bool = True  # rests, failure streaks and demotions survive a restart
    ledger: bool = True  # count requests and tokens per provider per day (capacity.sqlite)
    usage_order: bool = True  # providers short of today's capacity give way; one that cannot afford a call goes last
    low_headroom: float = Field(0.15, ge=0, le=1)  # share of the day's capacity below which a provider gives way
    complexity: bool = True  # simple turns use a provider's small model (gpt-oss-20b on Groq), hard ones the large
    learned: bool = True  # which provider succeeds fastest per kind of request, learned from use, biases the order
    pseudonymize: bool = True  # providers that may train on prompts see placeholders for names, e-mails, phones
    brownout: bool = True  # when little of today's capacity is left: shorter answers, no optional model work
    brownout_below: float = Field(0.15, ge=0, le=1)
    hedge: bool = False  # start the next provider too when the first is slow; costs a second call's quota
    hedge_after_s: float = Field(2.5, ge=0, le=60)


class TurnConfig(BaseModel):
    """How long a spoken turn may wait, and what it says while waiting."""
    first_token_deadline_s: float = Field(6.0, ge=0, le=300)  # fast tier, per model call; 0 = wait as providers allow
    first_clause_words: int = Field(8, ge=0, le=40)  # the first spoken piece may stop at a comma after this many words; 0 = whole sentences only
    notice_after_s: float = Field(3.0, ge=0, le=60)  # say "One moment." once if nothing was said by then; 0 = never
    coalesce_repeats: bool = True  # saying the same request again while it is being worked on does not restart it
    repeat_window_s: float = Field(15.0, ge=0, le=120)
    repeat_overlap: float = Field(0.7, gt=0, le=1)
    request_diet: bool = True  # only the newest page reading in full; essential browser tools on follow-ups
    keep_full_turns: int = Field(4, ge=0, le=50)  # older turns are sent as one line each (no model); 0 = all in full
    # Telegram chats keep much more than the voice window: the newest `chat_full_messages` messages in full, up to
    # `chat_compressed_messages` messages back one line per turn, older than that a few words per turn (no model).
    chat_full_messages: int = Field(20, ge=2, le=200)
    chat_compressed_messages: int = Field(100, ge=2, le=1000)
    chat_keep_messages: int = Field(300, ge=20, le=2000)  # the most messages of a chat kept in memory
    chat_keep_hours: int = Field(72, ge=1, le=720)  # ... and for how long (private turns still follow memory.window_min)
    notice_skips_streaming: bool = True  # no "One moment." when the model has already started its answer
    latency_alarm_ms: int = Field(5000, ge=0, le=60000)  # warn when this many turns in a row start slower; 0 = off
    latency_alarm_turns: int = Field(3, ge=1, le=20)
    # Speed-ups that are off until you switch them on (docs/getting-started.md, "Faster replies"):
    parallel_reads: bool = True  # several lookups asked for in one round (weather, search, time, ...) run at the same time
    fold_history: bool = False  # turns that fall out of the window become a short model-written summary (foldkit.py); judge by ear
    fold_every_turns: int = Field(3, ge=1, le=20)  # fold once this many turns have fallen out
    fold_max_chars: int = Field(1200, ge=200, le=6000)  # the summary never grows past this
    check_claims: bool = True  # "I sent it" with no send behind it is corrected out loud, straight after (claimcheck.py)
    stable_prefix: bool = False  # the notes you change go after everything that stays the same, so providers can reuse the start of the prompt
    lean_prompt: bool = False  # the short wording of the fixed rules, whether or not it passed its comparison; judge by ear


def default_providers() -> list[ProviderConfig]:
    return [
        ProviderConfig(name="groq", base_url="https://api.groq.com/openai/v1", model="openai/gpt-oss-120b",
                       small_model="openai/gpt-oss-20b", limits=Limits(rpm=30, tpm=8000, rpd=1000, tpd=200000)),
        ProviderConfig(name="cerebras", base_url="https://api.cerebras.ai/v1", model="llama-3.3-70b",
                       allow_sensitive=False, policy_verified=False),
        ProviderConfig(
            name="gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            model="gemini-3.8-flash",
            trains_on_data=True,
            allow_sensitive=False,
        ),
        ProviderConfig(
            name="openrouter",
            base_url="https://openrouter.ai/api/v1",
            model="nvidia/nemotron-3-super-120b-a12b:free",
            trains_on_data=True,
            allow_sensitive=False,
            limits=Limits(rpm=20, rpd=50),
        ),
        ProviderConfig(
            name="mistral",
            base_url="https://api.mistral.ai/v1",
            model="mistral-small-latest",
            trains_on_data=True,
            allow_sensitive=False,
        ),
        ProviderConfig(name="nvidia", base_url="https://integrate.api.nvidia.com/v1",
                       model="nvidia/nemotron-3-super-120b-a12b", limits=Limits(rpm=40), allow_sensitive=False,
                       policy_verified=False),
        ProviderConfig(
            name="nvidia-deep",
            base_url="https://integrate.api.nvidia.com/v1",
            model="moonshotai/kimi-k3",
            tier="deep",
            key_name="nvidia",
            extra={"reasoning_effort": "max"},
            timeout_s=180.0,
            limits=Limits(rpm=40),
            allow_sensitive=False,
            policy_verified=False,
        ),
        ProviderConfig(
            name="nvidia-gemma",
            base_url="https://integrate.api.nvidia.com/v1",
            model="google/gemma-4-31b-it",
            key_name="nvidia",
            limits=Limits(rpm=40),
            allow_sensitive=False,
            policy_verified=False,
        ),
        ProviderConfig(name="anthropic", kind="anthropic", model="claude-sonnet-5-5", enabled=False, paid=True),
    ]


CLAUDE_BRAIN = ("claude", "claude-deep")
CLAUDE_BRAIN_LEGACY = ("claude-opus",)  # the first version's name for the thinking-mode provider


def claude_brain_providers() -> list[ProviderConfig]:
    """Claude through your own Claude Code login (`claude -p`), as a ladder: most turns Haiku at low effort, medium
    ones Haiku at high effort, hard ones Sonnet at low effort, thinking mode Sonnet at high effort. Opus is never
    chosen on its own: only when you say "use opus ...". No key; the subscription is the budget. Data terms depend on
    your claude.ai privacy setting, so it is marked as possibly training and never gets private turns (set
    trains_on_data = false and policy_verified = true once you have checked)."""
    common = dict(kind="claude_cli", requires_key=False, trains_on_data=True, allow_sensitive=False,
                  policy_verified=False, timeout_s=120.0)
    plan = {"small": ["haiku", "low"], "medium": ["haiku", "high"], "large": ["sonnet", "low"]}
    return [
        ProviderConfig(name="claude-deep", model="sonnet", tier="deep", extra={"effort": "high", "reserve": 0.9},
                       **common),
        ProviderConfig(name="claude", model="haiku", tier="fast", extra={"effort": "low", "reserve": 0.9, "plan": plan},
                       **common),
    ]


class Config(BaseModel):
    providers: list[ProviderConfig] = Field(default_factory=default_providers)
    personality: PersonalityConfig = Field(default_factory=PersonalityConfig)
    stt: SttConfig = Field(default_factory=SttConfig)
    tts: TtsConfig = Field(default_factory=TtsConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    proactive: ProactiveConfig = Field(default_factory=ProactiveConfig)
    gmail_watch: GmailWatchConfig = Field(default_factory=GmailWatchConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    router: RouterConfig = Field(default_factory=RouterConfig)
    turn: TurnConfig = Field(default_factory=TurnConfig)
    frugal: FrugalConfig = Field(default_factory=FrugalConfig)
    jobs: JobsConfig = Field(default_factory=JobsConfig)
    debug: DebugConfig = Field(default_factory=DebugConfig)
    offline: OfflineConfig = Field(default_factory=OfflineConfig)
    instant: InstantConfig = Field(default_factory=InstantConfig)
    profile: ProfileConfig = Field(default_factory=ProfileConfig)
    update: UpdateConfig = Field(default_factory=UpdateConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)
    coach: CoachConfig = Field(default_factory=CoachConfig)
    finance: FinanceConfig = Field(default_factory=FinanceConfig)
    life: LifeConfig = Field(default_factory=LifeConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    overlay: OverlayConfig = Field(default_factory=OverlayConfig)
    vault: VaultConfig = Field(default_factory=VaultConfig)
    butler: ButlerConfig = Field(default_factory=ButlerConfig)
    study: StudyConfig = Field(default_factory=StudyConfig)
    productivity: ProductivityConfig = Field(default_factory=ProductivityConfig)
    recorder: RecorderConfig = Field(default_factory=RecorderConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    system: SystemConfig = Field(default_factory=SystemConfig)
    dev: DevConfig = Field(default_factory=DevConfig)
    create: CreateConfig = Field(default_factory=CreateConfig)
    mcp: McpConfig = Field(default_factory=McpConfig)
    shell: ShellConfig = Field(default_factory=ShellConfig)
    selfcheck: SelfcheckConfig = Field(default_factory=SelfcheckConfig)
    integrations: IntegrationsConfig = Field(default_factory=IntegrationsConfig)
    ops: OpsConfig = Field(default_factory=OpsConfig)
    local_llm: LocalLlmConfig = Field(default_factory=LocalLlmConfig)
    plugins: dict[str, dict] = Field(default_factory=dict)
    preferred_provider: str = ""  # provider the user picked by voice ("switch to gemini"); empty: automatic order
    toolkit_routing: bool = True  # offer only the tools a request needs (false: every tool, every turn)
    clock_24h: bool = False  # times on the display and in speech: 15:10 instead of 3:10 PM
    terminal_ui: bool = True  # full-screen status display when run in a terminal
    terminal_reduced_motion: bool = False  # still orb and no spinners; the mic meter keeps moving
    terminal_images: Literal["auto", "off", "sixel", "kitty"] = "auto"  # real album covers where the terminal can show them
    terminal_cover_size: int = Field(18, ge=10, le=36)  # the real cover's width in cells, about 9 pixels each (28 is about 256)
    terminal_hotkeys: bool = True  # on the display, with nothing typed: the mute key and the listen key work
    terminal_mouse: bool = True  # on the display, the Mute and Listen buttons can be clicked (off: text can be selected as usual)
    terminal_mute_key: str = "m"  # one character, or "space"; pressed with nothing typed it mutes and unmutes the microphone
    terminal_listen_key: str = "space"  # one character, or "space"; pressed with nothing typed it starts listening, like saying the name
    api_port: int = Field(7777, ge=1024, le=65535)

    @field_validator("terminal_mute_key", "terminal_listen_key")
    @classmethod
    def _one_key(cls, value: str) -> str:
        raw = str(value)
        key = "space" if raw == " " else raw.lower()
        if key == "space" or (len(key) == 1 and key.isprintable() and not key.isspace()):
            return key
        raise ValueError('use one character such as "m", or the word "space"')

    @model_validator(mode="after")
    def _keys_differ(self) -> "Config":
        if self.terminal_mute_key == self.terminal_listen_key:
            raise ValueError("terminal_mute_key and terminal_listen_key must be different keys")
        return self


class ConfigStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.path = self.directory / "config.toml"
        self._config = Config()
        self._listeners: list[Callable[[Config], None]] = []

    @property
    def config(self) -> Config:
        return self._config

    def load(self) -> Config:
        if self.path.exists():
            try:
                with self.path.open("rb") as f:
                    self._config = Config.model_validate(tomllib.load(f))
            except tomllib.TOMLDecodeError as e:
                raise ConfigError(f"{self.path}: {e}") from e
            except ValidationError as e:
                raise ConfigError(f"{self.path}: " + "; ".join(explain_validation(e))) from e
        timefmt.configure(self._config.clock_24h)
        return self._config

    def update(self, data: dict) -> Config:
        new = Config.model_validate(data)
        text = tomli_w.dumps(new.model_dump(mode="json", exclude_none=True))
        atomic_write_text(self.path, text, 0o644)
        self._config = new
        timefmt.configure(new.clock_24h)
        logger = logging.getLogger(__name__)
        for cb in list(self._listeners):
            try:
                cb(new)
            except Exception:
                logger.exception("config listener failed")
        return new

    def subscribe(self, cb: Callable[[Config], None]) -> None:
        self._listeners.append(cb)
