"""Every on/off setting in one list, with a plain label, so none of them needs the config file to change.

Walks the settings and returns the booleans; setting one goes through the normal config update (validated, saved,
listeners told). Plugins are switched in their own page; secrets are never here.
"""
from __future__ import annotations

from pydantic import BaseModel

LABELS = {
    "router.warm_on_wake": "Open the connection to the model provider the moment the name is heard",
    "turn.stable_prefix": "Put the notes that change after everything that stays the same in the prompt",
    "turn.lean_prompt": "Use the short wording of the fixed prompt rules",
    "stt.auto_model": "Pick the local recogniser's size from this computer",
    "audio.live_barge_in": "Let a loud voice interrupt the live voice while it speaks",
    "profile.enabled": "Add the answers from your profile that relate to a request to what the model sees",
    "clock_24h": "Show and say times on a 24-hour clock (15:10) instead of 3:10 PM",
    "terminal_hotkeys": "On the terminal display, the mute and listen keys work when nothing is typed",
    "terminal_mouse": "On the terminal display, the Mute and Listen buttons can be clicked",
    "audio.auto_sensitivity": "Adapt the microphone to the room: the volume that counts as speech follows the background noise",
    "audio.listen_duck": "Turn other audio down a lot while Minty listens, and back up afterwards",
    "audio.speculative_stt": "Recognise your words during the pause, so the answer starts sooner",
    "instant.enabled": "Answer simple questions (time, maths, unit conversion) at once, without asking a model",
    "instant.actions": "Do simple actions (timers, volume, pause the music) at once, without asking a model",
    "safety.privacy_mode": "Privacy mode: nothing leaves this computer except to providers that don't train on it",
    "safety.confirm_risky": "Ask before risky actions (money, passwords and deleting are always asked)",
    "safety.audit": "Keep an audit trail of decisions",
    "safety.approval_inbox": "Keep every question as an approval record",
    "safety.block_secret_egress": "Stop tool calls that would send a stored key",
    "safety.mandates": "Do not ask again about what you just asked for (never for money, keys, deletion or people)",
    "safety.honeytokens": "Plant a decoy key file to catch anything hunting for keys",
    "safety.mask_pii_in_traces": "Mask e-mails, phones, cards and ID numbers in traces",
    "safety.audit_installs": "Say what is known about a package before installing it",
    "safety.weekly_review": "A short permission review note every Sunday",
    "health.hub": "Keep health data (workouts, food, sleep, body) in a local store; nothing is collected until you agree",
    "health.hevy": "Fetch new workouts from Hevy every few hours (read-only; needs your Hevy API key)",
    "health.hc_bridge": "Import health exports (Hevy, Cronometer, Strava archive, Health Connect) sent to the Telegram bot",
    "coach.enabled": "The fitness and nutrition coach explains your own numbers (needs your training data)",
    "coach.nutrition": "Include food, protein, maintenance estimate, water and sleep in coaching",
    "coach.briefs": "Send a pre-workout brief and a post-workout debrief to Telegram",
    "coach.planning": "Planning and review: programme builder, deload weeks, PR board, streaks, goal feasibility",
    "coach.air_quality": "Check the air quality before outdoor training (sends your coordinates to Open-Meteo)",
    "coach.age_rules": "No weight-loss, deficit, fasting, BMI or weigh-in coaching for under 18 or unknown age",
    "overlay.hud": "Show a small HUD (state, captions, timers) while another window is active (run: minty overlay)",
    "voice.corrections": "\"No, I said ...\" re-asks with your correction",
    "voice.vocabulary": "Teach the speech recogniser the words you correct",
    "voice.playback_controls": "\"Go on\", \"skip\", \"repeat that\", \"shorter\" by voice",
    "voice.dictation": "\"Start dictation\": what you say next is written down, not run",
    "voice.pronunciations": "Use your pronunciation dictionary when speaking",
    "voice.whisper": "Whisper mode: speak quietly",
    "voice.ambient_volume": "Match my volume to the room",
    "voice.pace_matching": "Match my speaking pace to yours",
    "voice.time_of_day": "A calmer voice in the evening",
    "voice.duck_music": "Lower the music while I speak instead of pausing it",
    "voice.progress_narration": "A short spoken update while a long job runs",
    "voice.bilingual": "Understand and answer in Hindi when you switch",
    "voice.speaker_check": "A convenience voice check before sensitive actions",
    "life.review": "Dashboards, a 60-second briefing, a weekly review and goals from what is logged",
    "life.checkins": "A two-question check-in in Telegram each evening",
    "life.review_push": "Send the weekly review to Telegram on Sundays",
    "finance.enabled": "Track your money privately: quick add, budgets, goals, subscriptions (read-only; never pays)",
    "finance.insights": "Money insights: budget alerts, subscription audit, forecast, safe to spend, scam check",
    "finance.bill_reminders": "Remind you when a bill you set is due within three days and not paid",
    "telegram.enabled": "Answer you in Telegram (needs a bot token and a paired chat; restart to apply)",
    "telegram.class3_passphrase": "Allow the biggest approvals from Telegram with a passphrase (leave off to keep them on the laptop)",
    "telegram.relay": "Accept and send files between this machine's bot and another one you listed (both must enable it)",
    "telegram.screenshots": "Send browser screenshots to Telegram (checked for secrets on the laptop first)",
    "telegram.screenshots_unchecked": "Send screenshots even when they cannot be checked first (no text reader installed)",
    "telegram.briefs": "Send the morning and evening brief to Telegram as a message",
    "offline.enabled": "Answer what needs no model while the internet is down",
    "jobs.resume": "Let interrupted jobs be resumed from their checkpoint",
    "memory.auto_remember": "Save lasting personal facts you mention without being asked",
    "memory.gist_index": "Find older memory by words instead of pasting it into every request",
    "toolkit_routing": "Offer only the tools a request needs",
    "terminal_ui": "Full-screen status display in the terminal",
    "terminal_reduced_motion": "Still orb and no spinners in the terminal",
    "update.check": "Once a day, look for a newer Minty release on GitHub and say so (nothing is installed)",
    "update.auto_install": "Install a newer release by itself (only for a repository whose owner you trust completely)",
    "update.verify_signed": "Only install a release whose git tag is signed by a key your git knows",
    "terminal_hotkeys": "Mute and Listen keys on the terminal display (when nothing is being typed)",
    "terminal_mouse": "Click the Mute and Listen buttons on the terminal display (off lets you select text with the mouse)",
    "turn.request_diet": "Send only the newest page reading and the essential tools on follow-ups",
    "turn.notice_skips_streaming": "No 'One moment' once the model is already answering",
    "turn.coalesce_repeats": "Treat a repeated request as the same one while it is running",
}
SKIP_SECTIONS = {"providers", "plugins", "debug"}


def _humanise(path: str) -> str:
    return path.split(".")[-1].replace("_", " ").capitalize()


def _walk(model: BaseModel, prefix: str = ""):
    for name in type(model).model_fields:
        if not prefix and name in SKIP_SECTIONS:
            continue
        value = getattr(model, name)
        path = f"{prefix}{name}"
        if isinstance(value, bool):
            yield path, value
        elif isinstance(value, BaseModel):
            yield from _walk(value, path + ".")


def list_flags(config) -> list[dict]:
    out = []
    for path, value in _walk(config):
        group = path.split(".")[0] if "." in path else "general"
        out.append({"path": path, "value": value, "group": group, "label": LABELS.get(path, _humanise(path))})
    return sorted(out, key=lambda f: (f["group"], f["path"]))


def set_flag(store, path: str, value: bool) -> bool:
    """False when `path` is not a switch; raises ValueError for a value that is not on/off."""
    if path not in {f["path"] for f in list_flags(store.config)}:
        return False
    if not isinstance(value, bool):
        raise ValueError("a switch is on or off")
    data = store.config.model_dump(mode="json")
    node = data
    parts = path.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value
    store.update(data)
    return True
