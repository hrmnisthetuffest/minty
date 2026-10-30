import argparse
import asyncio
from datetime import datetime
import getpass
import logging
import os
import stat
import sys
import time
from pathlib import Path

from minty_core import __version__
from minty_core._toml import tomllib
from minty_core.config import AudioConfig, ConfigError, ConfigStore
from minty_core.context import AppContext, build_context
from minty_core.audio.stt import effective_local_model
from minty_core.audio.vad import build_frame_vad, voiced_ms
from minty_core.jobs import interrupted_notice
from minty_core.safemode import SAFE_PLUGINS, BootLog, is_deliberate_stop, start_in_safe_mode
from minty_core.supervisor import VoiceSupervisor
from minty_core.keyimport import extract_keys
from minty_core import instance
from minty_core.keystore import SecretsDamaged, SecretsStore
from minty_core.logredact import install_uvicorn_redaction
from minty_core.paths import config_dir

log = logging.getLogger("minty")
_background: set[asyncio.Task] = set()


def _log_task_failure(task: asyncio.Task, what: str) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.error("%s failed", what, exc_info=task.exception())


def _canary_now(ctx) -> str:
    """Start the provider check in the background and say what is happening; the result is spoken when done."""
    async def go() -> None:
        try:
            ctx.bus.publish("notify", text=await ctx.services["canary_now"]())
        except Exception:  # noqa: BLE001
            log.warning("provider check failed", exc_info=True)

    asyncio.get_running_loop().create_task(go())
    return "Checking the providers now. I'll tell you what I find."


def _on_conversation(ctx):
    """Called for every real conversation: the first one of the day starts the day-start routine (which speaks once
    the conversation is over)."""
    def hook() -> None:
        ctx.services["daystart"].on_first()
    return hook


def _button_action(ctx):
    """What the Mute and Listen buttons (keys or clicks on the display) do."""
    def act(name: str) -> None:
        pipe = ctx.pipeline
        if pipe is None:
            return
        if name == "mute":
            pipe.muted = not pipe.muted
        elif name == "listen" and not pipe.listen_now() and pipe.muted:
            ctx.bus.publish("notice", message="Muted. Press M to unmute.")
    return act


def _config_changes_reply(ctx) -> str:
    changes = ctx.services["config_snapshots"].last_change()
    if not changes:
        return "I have no earlier version of your settings to compare with."
    shown = "; ".join(c.words() for c in changes[:4])
    return f"The last change: {shown}." + (f" And {len(changes) - 4} more." if len(changes) > 4 else "")


FREE_FORM = ("tune", "update", "instant")


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="minty-core")
    parser.add_argument("--version", action="version", version=f"Minty {__version__}")
    parser.add_argument("--no-panel", action="store_true", help="do not start the web panel")
    parser.add_argument("--safe", action="store_true", help="start in safe mode: only the basic plugins, for this run")
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="start Minty and the web panel (default)")
    run.add_argument("--no-panel", action="store_true", default=argparse.SUPPRESS, help="do not start the web panel")
    run.add_argument("--safe", action="store_true", default=argparse.SUPPRESS,
                     help="start in safe mode: only the basic plugins, for this run")
    onboard_cli = sub.add_parser("onboard", help="guided setup, one step at a time (keys, voice, safety, phone)")
    onboard_cli.add_argument("--step", default="", help="run just one step: system, brain, voice, you, safety, phone, "
                                                        "extras or finish")
    onboard_cli.add_argument("--again", action="store_true", help="start from the first step")
    sub.add_parser("setup", help="the same as onboard")
    tune_cli = sub.add_parser("tune", help="what this computer is and the settings that suit it (--apply to use them)",
                              add_help=False)
    tune_cli.add_argument("rest", nargs=argparse.REMAINDER)
    update_cli = sub.add_parser("update", help="check GitHub for a newer release and install it (--check to only look, --yes to skip the question)",
                                add_help=False)
    update_cli.add_argument("rest", nargs=argparse.REMAINDER)
    instant_cli = sub.add_parser("instant", help="what is answered without a model (or try a phrase: minty instant \"what is 12 times 8\")",
                                 add_help=False)
    instant_cli.add_argument("rest", nargs=argparse.REMAINDER)
    unit = sub.add_parser("service-unit", help="print the systemd user unit that keeps Minty running")
    unit.add_argument("--root", default="", help="the checkout the unit should run (default: this one)")
    sub.add_parser("overlay", help="run the small on-screen HUD (shows only while another window is active)")
    transfer = sub.add_parser("transfer", help="encrypted export and import of everything: export, verify, import, "
                                              "join", add_help=False)
    transfer.add_argument("rest", nargs=argparse.REMAINDER)
    butler_cli = sub.add_parser("butler", help="butler settings you alone can make: standing approvals", add_help=False)
    butler_cli.add_argument("rest", nargs=argparse.REMAINDER)
    mcp_cli = sub.add_parser("mcp", help="MCP servers and which of their tools may be used (you alone decide)", add_help=False)
    mcp_cli.add_argument("rest", nargs=argparse.REMAINDER)
    notes = sub.add_parser("notes", help="your notes folder in the terminal: list, search, show, new, edit, inbox",
                           add_help=False)
    notes.add_argument("rest", nargs=argparse.REMAINDER)
    aec = sub.add_parser("aec", help="PipeWire echo cancellation for speakers: on, off or status")
    aec.add_argument("action", choices=["on", "off", "status"])
    sub.add_parser("telegram-pair", help="print a one-time code that pairs your Telegram chat with your bot")
    sub.add_parser("telegram-passphrase", help="set the passphrase that allows class-3 approvals from Telegram")
    brain = sub.add_parser("claude-brain", help="use Claude (your Claude Code login) as the main brain: on, off or status")
    brain.add_argument("action", choices=["on", "off", "status"])
    show = sub.add_parser("prompts-show", help="print the prompt modules that would be used, with their sizes")
    show.add_argument("--channel", choices=["voice", "telegram"], default="voice")
    show.add_argument("--tier", choices=["fast", "deep"], default="fast")
    show.add_argument("--kit", action="append", default=[], help="a tool group in play, e.g. research (repeatable)")
    pimport = sub.add_parser("profile-import", help="bring old free-form profile notes into the 500 questions (a proposal; --apply writes it)")
    pimport.add_argument("path", help="the notes file")
    pimport.add_argument("--apply", action="store_true", help="write the confident matches (nothing already answered is changed)")
    sub.add_parser("local-llm-test", help="measure the optional local model and adopt it only if it is fast enough")
    for name, text in (("set-pin", "set the web panel login PIN (4 to 8 digits)"), ("set-password", "the same as set-pin")):
        sub.add_parser(name, help=text).add_argument("--allow-easy", action="store_true", help="accept a PIN that is easy to guess (0000, 1234)")
    sub.add_parser("spotify-login", help="sign in to Spotify once so Minty can read your playlists")
    set_key = sub.add_parser("set-key", help="store an API key without echoing it")
    set_key.add_argument("name", help="provider name, e.g. groq")
    set_key.add_argument("--show", action="store_true", help="show the key as you type or paste it (easier to check; it stays in the terminal's history)")
    import_help = "import API keys from a plain-text notes file; existing keys with the same name are replaced"
    import_keys = sub.add_parser("import-keys", help=import_help, description=import_help)
    import_keys.add_argument("path", help="file holding your keys")
    if argv and argv[0] in FREE_FORM:  # their own options (--lite, --check, a whole sentence): argparse must not read them
        args = parser.parse_args([argv[0]])
        args.rest = list(argv[1:])
        return args
    args = parser.parse_args(argv)
    if args.command == "set-password":  # the old name of set-pin
        args.command = "set-pin"
    if args.command is None:
        args.command = "run"
    return args


def diff_audio(old: AudioConfig, new: AudioConfig) -> set[str]:
    return {name for name in AudioConfig.model_fields if getattr(old, name) != getattr(new, name)}


def build_voice_pipeline(ctx: AppContext):
    from minty_core.audio.music import MusicWatcher

    music = MusicWatcher()
    from minty_core.voice.adapt import Ducker

    listen_ducker = Ducker(state_path=ctx.config_dir / "ducked.json")
    listen_ducker.recover()  # the last run was killed while the sound was turned down: put it back now
    ctx.services["music_watcher"] = music  # song recognition counts players Minty paused for a conversation
    from minty_core.audio.devices import OpenWakeWordDetector, SoundDeviceInput, SoundDevicePlayer, StreamingPlayer
    from minty_core.live import LiveMode
    from minty_core.plugins.host import FilteredHost
    from minty_core.audio.endpointer import EnergyEndpointer
    from minty_core.audio.stt import DynamicSTT
    from minty_core.vault.kit import glossary_hint
    from minty_core.audio.streaming import build_streaming
    from minty_core.audio.tts import ConfiguredTTS, voices_dir
    from minty_core.audio.wakewords import whisper_transcriber
    from minty_core.commands import CommandRouter
    from minty_core.coach.commands import CoachCommands
    from minty_core.finance_commands import FinanceCommands
    from minty_core.health import HealthCommands
    from minty_core.productivity.commands import ProductivityCommands
    from minty_core.pipeline import VoicePipeline

    store = ctx.store
    audio_in = SoundDeviceInput(store)

    def make_wake(cfg):
        if cfg.audio.wake_mode == "words":
            from minty_core.audio.wakewords import TranscriptWakeDetector, whisper_transcriber

            return TranscriptWakeDetector(
                whisper_transcriber(effective_local_model(cfg.stt)), lambda: store.config.audio,
                on_partial=lambda text: ctx.bus.publish("partial", text=text), music=music.is_playing,
                speech_check=voiced_ms, vad=build_frame_vad(),
            )
        from datetime import datetime as _now

        from minty_core.nightmode import wake_boost

        return OpenWakeWordDetector(cfg.audio.wake_word, cfg.audio.wake_threshold,
                                    boost=lambda: wake_boost(_now.now(), store.config.ops))

    wake = make_wake(store.config)

    def make_stopper():
        if store.config.audio.wake_mode != "words" or not store.config.audio.hands_free_stop:
            return None
        from minty_core.audio.wakewords import TranscriptWakeDetector, whisper_transcriber

        stop_phrases = ["stop", "wait", "hold on", "cancel", "never mind", "be quiet", "quiet"]
        return TranscriptWakeDetector(
            whisper_transcriber(effective_local_model(store.config.stt)),
            lambda: store.config.audio.model_copy(update={"wake_phrases": stop_phrases, "live_captions": False}),
            speech_check=voiced_ms, vad=build_frame_vad())

    pipeline = VoicePipeline(
        audio_in=audio_in, wake=wake, endpointer=EnergyEndpointer(lambda: store.config.audio, vad=build_frame_vad()),
        stt=DynamicSTT(store, ctx.secrets, extra_hints=lambda: " ".join(filter(None, (
            ctx.services["voice_extras"].stt_hint() if "voice_extras" in ctx.services else "",
            glossary_hint(ctx.services["vault"], store.config) if "vault" in ctx.services else "")))), tts=ConfiguredTTS(store, secrets=ctx.secrets, private=lambda: bool(
            getattr(ctx.agent, "force_private", False)
            or any(getattr(m, "private", False) for m in getattr(ctx.agent, "history", [])))),
        player=SoundDevicePlayer(store),
        agent=ctx.agent, bus=ctx.bus, store=store,
        commands=CommandRouter(ctx.agent, store, restart=True, prompts=ctx.services.get("prompts"), router=ctx.router,
                               jobs=ctx.services.get("jobs"),
                               proactive=ctx.services.get("proactive_control"),
                               capacity=ctx.services.get("brownout"), where=ctx.services.get("where"),
                               receipt=ctx.services.get("receipt"), approvals=ctx.services.get("approvals"),
                               lockdown=ctx.services.get("lockdown"), allow_unlock=True,
                               receipts=ctx.services.get("receipts"),
                               permissions=ctx.services.get("permissions"), bootlog=ctx.services.get("bootlog"),
                               destinations=lambda plugin: (ctx.services["destinations"].today_summary() if plugin == "__today__"
                                                            else ctx.services["destinations"].summary(plugin)),
                               health_data=(HealthCommands(ctx.services["health"])
                                            if ctx.services.get("health") is not None else None),
                               focus_end=lambda: _end_focus(ctx),
                               butler=ctx.services["butler"].capture if "butler" in ctx.services else None,
                               productivity=(ProductivityCommands(ctx.services["productivity"], ctx.bus.publish).handle
                                             if ctx.services.get("productivity") is not None else None),
                               finance=(FinanceCommands(ctx.services["finance"],
                                                        enabled=lambda: store.config.finance.enabled,
                                                        account=lambda: store.config.finance.default_account)
                                        if ctx.services.get("finance") is not None else None),
                               coach=(CoachCommands(ctx.services["health"],
                                                    enabled=lambda: store.config.coach.enabled)
                                      if ctx.services.get("health") is not None else None),
                               offline=ctx.services["offline"].probe,
                               config_changes=lambda: _config_changes_reply(ctx),
                               backup_now=lambda: ctx.services["backup_now"]() if "backup_now" in ctx.services
                               else "not ready yet",
                               health=lambda: _health_digest(ctx, ctx.config_dir),
                               canary=lambda: _canary_now(ctx),
                               power=lambda: ctx.services["power"].words() if "power" in ctx.services
                               else "I haven't read the power state yet."),
        live=LiveMode(store, ctx.secrets, FilteredHost(ctx.host, exclude_plugins=("agents", "installer", "subagents"),
                                                       allow_risky=True),  # risky actions are parked for a spoken yes
                      ctx.agent, ctx.bus, StreamingPlayer(store), router=ctx.router, guard=ctx.services.get("guard")),
        captions=whisper_transcriber(effective_local_model(store.config.stt)), music=music, listen_ducker=listen_ducker, warm=ctx.router.warm, on_restart=request_restart,
        speech_check=voiced_ms, on_conversation=_on_conversation(ctx),
        streaming=build_streaming(store.config.audio, voices_dir()),
    )
    from minty_core.voice.turnjudge import make_turn_judge

    def _last_reply() -> str:
        return next((m.content for m in reversed(ctx.agent.history) if m.role == "assistant" and m.content), "")

    def _private_now() -> bool:
        return bool(store.config.safety.privacy_mode or ctx.agent.force_private
                    or ctx.services.get("is_private", lambda: False)())

    pipeline.turn_judge = make_turn_judge(ctx.router, store, last_reply=_last_reply, private=_private_now)
    previous = {"audio": store.config.audio}

    def on_config_change(cfg):
        changed = diff_audio(previous["audio"], cfg.audio)
        previous["audio"] = cfg.audio
        if {"wake_mode", "wake_word", "wake_threshold", "wake_phrases"} & changed:
            pipeline.wake = make_wake(cfg)
        if "hands_free_stop" in changed:
            pipeline.stopper = make_stopper()
        if "input_device" in changed:
            task = asyncio.get_running_loop().create_task(audio_in.restart())
            _background.add(task)
            task.add_done_callback(_background.discard)
            task.add_done_callback(lambda t: _log_task_failure(t, "audio input restart"))

    pipeline.stopper = make_stopper()
    from minty_core.voice.extras import VoiceExtras

    pipeline.voice = VoiceExtras(store, ctx.config_dir)

    async def save_dictation(text: str) -> str:
        out = await ctx.host.call("journal_add_entry", {"text": f"Dictated: {text}"})
        return "Saved to your journal." if not str(out).startswith("Error") else "I couldn't save that just now."

    pipeline.voice.save_note = save_dictation
    ctx.services["voice_extras"] = pipeline.voice
    store.subscribe(on_config_change)
    return pipeline


def compaction_due(ctx, now: float | None = None) -> bool:
    """Background model work runs at night (or once a day is overdue) and only with enough capacity left."""
    cfg = ctx.store.config.memory
    now = time.time() if now is None else now
    brownout = ctx.services.get("brownout")
    share = brownout.capacity().share if brownout is not None else None
    if share is not None and share < cfg.compact_min_share:
        return False
    if cfg.compact_from_hour <= time.localtime(now).tm_hour < cfg.compact_to_hour:
        return True
    overdue = time.strftime("%Y-%m-%d", time.localtime(now - 2 * 86400))
    return any(day <= overdue for day in ctx.services["archive"].pending_days(now))


async def compact_once(ctx) -> None:
    """Compress finished days and months (optional model work: it waits while capacity is low)."""
    cfg = ctx.store.config.memory
    if not cfg.compress:
        return
    brownout = ctx.services.get("brownout")
    if brownout is not None and brownout.active():
        log.info("free model capacity is low; memory compaction waits")
        return
    if cfg.batch_reflection and not compaction_due(ctx):
        return
    batch = {"combined": ctx.services["summarize_and_reflect"], "on_learned": ctx.services["keep_learned"]} \
        if cfg.batch_reflection else {}
    try:
        done = await ctx.services["archive"].compact(
            ctx.services["summarize"], ctx.services["reflect"], ctx.services.get("gist_sink"), **batch)
        if done:
            log.info("memory compacted: %s", ", ".join(done))
    except Exception:
        log.exception("memory compaction failed; will retry")


async def _compaction_loop(ctx) -> None:
    """Runs shortly after start, then hourly. Raw transcripts are never touched."""
    await asyncio.sleep(30)
    while True:
        await compact_once(ctx)
        await asyncio.sleep(3600)


def _health_digest(ctx, directory: Path) -> str:
    from minty_core.heartbeat import digest
    from minty_core.housekeeping import DiskGuard

    boot = ctx.services.get("bootlog")
    starts, unclean = boot.stats() if boot is not None else (0, 0)
    ops = ctx.store.config.ops
    folder = Path(ops.backup_folder).expanduser() if ops.backup_folder else directory / "backups"
    backups = sorted(folder.glob("minty-backup-*.bak")) if folder.exists() else []
    text, _facts = digest(
        trace_dir=ctx.services["tracer"].directory, now=datetime.now(), starts=starts, unclean_starts=unclean,
        providers=ctx.router.status(), disk_free_mb=DiskGuard(directory).measure().free_mb,
        last_backup=backups[-1].stat().st_mtime if backups else None,
        interrupted_jobs=len(ctx.services["jobs"].interrupted))
    from minty_core.updates import key_reminder

    reminder = key_reminder(ctx.secrets.ages_days())
    return f"{text} {reminder}".strip()


async def _telegram_loop(ctx) -> None:
    """The phone gateway, when it is switched on and a bot token exists. A restart picks up a changed switch."""
    from minty_core.telegram.runtime import build_runtime

    runtime = build_runtime(ctx)
    if runtime is None:
        return
    ctx.services["telegram"] = runtime
    log.info("telegram gateway on (%d paired chat%s)", len(runtime.gateway.allowed),
             "" if len(runtime.gateway.allowed) == 1 else "s")
    try:
        await runtime.run()
    finally:
        runtime.stop()


async def _health_loop(ctx) -> None:
    """New Hevy workouts into the health hub every few hours, when switched on and a key and consent exist."""
    hub = ctx.services.get("health")
    if hub is None:
        return
    from minty_core.hevy import HevyPoller
    from minty_core.httpclient import get_client

    poller = HevyPoller(hub, lambda: ctx.secrets.get("hevy"), get_client())
    ctx.services["hevy"] = poller
    await asyncio.sleep(120)  # let start-up finish first
    while True:
        cfg = ctx.store.config.health
        if cfg.hevy:
            poller.poll_s = cfg.hevy_poll_hours * 3600
            try:
                result = await poller.sync_once()
            except Exception:  # noqa: BLE001 — a failed poll is retried at the next interval
                log.warning("hevy poll failed", exc_info=True)
            else:
                if result.added:
                    ctx.bus.publish("health", source="hevy", added=result.added)
                if result.error == "auth":
                    ctx.bus.publish("notify", text=result.message)
        await asyncio.sleep(poller.next_delay_s() if cfg.hevy else 600)


async def _coach_loop(ctx) -> None:
    """A debrief when a workout syncs and an optional pre-workout brief at a set time: to Telegram, never spoken."""
    hub = ctx.services.get("health")
    if hub is None:
        return
    from minty_core.coach.briefs import Coach

    coach = Coach(hub)
    while True:
        cfg = ctx.store.config.coach
        if cfg.enabled and cfg.briefs and hub.consent("training") and hub.profile().get("coach_paused") != "1":
            try:
                for d in coach.pending_debriefs():
                    ctx.bus.publish("coach", kind="debrief", text=d.text, workout_id=d.workout_id)
                brief = coach.scheduled_brief(datetime.now(), cfg.pre_workout_time)
                if brief is not None:
                    ctx.bus.publish("coach", kind="brief", text=brief.text)
            except Exception:  # noqa: BLE001 — a failed brief is skipped, not fatal
                log.warning("coach loop failed", exc_info=True)
        await asyncio.sleep(60)


async def _finance_loop(ctx, directory: Path) -> None:
    """A notice, once per bill and due date, when a bill you set is due within three days and not yet paid."""
    ledger = ctx.services.get("finance")
    if ledger is None:
        return
    import json

    from minty_core.finance_pack import pending_notices

    state_path = directory / "finance-reminded.json"
    while True:
        cfg = ctx.store.config.finance
        lock = ctx.services.get("lockdown")
        if cfg.enabled and cfg.bill_reminders and not (lock is not None and lock.active) and \
                datetime.now().hour >= 9:
            try:
                done = json.loads(state_path.read_text()) if state_path.exists() else {}
                texts, new = pending_notices(ledger, datetime.now().date(), done, insights=cfg.insights)
                for text in texts:
                    ctx.bus.publish("notify", text=text)
                if new != done:
                    state_path.write_text(json.dumps(new))
            except Exception:  # noqa: BLE001 — a missed reminder is retried at the next check
                log.warning("bill reminder check failed", exc_info=True)
        await asyncio.sleep(3600)


def _end_focus(ctx) -> str:
    focus, store = ctx.services.get("focus"), ctx.services.get("timers")
    if focus is None or not focus.active():
        return "You're not in focus mode."
    focus.stop()
    if store is not None:
        for t in store.all():
            if t.kind == "focus":
                store.cancel(t.id)
    return "Focus ended."


async def _focus_loop(ctx) -> None:
    """While focus mode runs: if a distracting window has been in front for a while, ask once (never block)."""
    from minty_core.focus import DistractionGuard
    from minty_core.overlay.xwin import ActiveWindowReader

    focus = ctx.services.get("focus")
    reader = ActiveWindowReader()
    if focus is None or not reader.available():
        return
    guard = DistractionGuard(focus, list(ctx.store.config.overlay.distraction_words))
    while True:
        if focus.active():
            guard.words = [w.lower() for w in ctx.store.config.overlay.distraction_words]
            question = await asyncio.to_thread(lambda: guard.check(reader.read()))
            if question:
                ctx.bus.publish("notify", text=question, urgent=True)
            await asyncio.sleep(2)
        else:
            guard.check(None)
            await asyncio.sleep(5)


async def _timers_loop(ctx) -> None:
    """Arm the timers that were on disk when the app started (and again after a plugin reload)."""
    while True:
        schedule = ctx.services.get("timers_schedule")
        if schedule is not None:
            try:
                schedule()
            except Exception:  # noqa: BLE001
                log.warning("could not arm the timers", exc_info=True)
        await asyncio.sleep(30)


async def _narration_loop(ctx) -> None:
    """A short spoken update while a long job runs (voice.progress_narration, off by default)."""
    from minty_core.voice.progress import Narrator

    narrator = Narrator()
    queue = ctx.bus.subscribe({"job"})
    try:
        while True:
            event = await queue.get()
            if ctx.store.config.voice.progress_narration:
                line = narrator.say(event)
                if line:
                    ctx.bus.publish("notify", text=line)
    finally:
        ctx.bus.unsubscribe(queue)


async def _life_loop(ctx) -> None:
    """The evening check-in and the Sunday review, to Telegram (never spoken); both off until switched on."""
    hub, ledger = ctx.services.get("health"), ctx.services.get("finance")
    if hub is None or ledger is None:
        return
    from minty_core.life import due_events

    while True:
        cfg = ctx.store.config.life
        if cfg.checkins or cfg.review_push:
            try:
                for event in due_events(hub, ledger, cfg.model_dump(), datetime.now()):
                    ctx.bus.publish(event.pop("type"), **event)
            except Exception:  # noqa: BLE001 — a missed check-in is not worth a crash
                log.warning("life loop failed", exc_info=True)
        await asyncio.sleep(60)


async def _vault_loop(ctx) -> None:
    """Each evening: filing suggestions for the inbox (nothing is moved) and decisions due for a second look."""
    provider = ctx.services.get("vault")
    if provider is None:
        return
    last = ""
    while True:
        cfg = ctx.store.config.vault
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if cfg.enabled and cfg.proposals and last != today and now.strftime("%H:%M") >= cfg.proposals_time:
            last = today
            try:
                for text in await asyncio.to_thread(provider.kit().evening, now.date()):
                    ctx.bus.publish("notify", text=text)
            except Exception:  # noqa: BLE001 — a missed suggestion is not worth a crash
                log.warning("vault loop failed", exc_info=True)
        await asyncio.sleep(60)


async def _butler_tick(ctx) -> None:
    """One pass: what is due (urgent ones at once, the rest in the digest), the Sunday review, the check-in."""
    provider = ctx.services.get("butler")
    say = lambda text: ctx.bus.publish("notify", text=text)  # noqa: E731
    urgent = lambda text: ctx.bus.publish("notify", text=text, urgent=True)  # noqa: E731 — focus mode never holds it
    await asyncio.to_thread(provider.deliver, say, urgent)
    if ctx.services.get("standing") is not None:
        await asyncio.to_thread(provider.weekly_review, ctx.services["standing"], say)
    await provider.check_in_step(ctx.services.get("checkin_send"), urgent)


async def _butler_loop(ctx) -> None:
    """Chores, follow-ups, renewals and check-ins that have come due (never while locked down)."""
    if ctx.services.get("butler") is None:
        return
    while True:
        lock = ctx.services.get("lockdown")
        if not (lock is not None and lock.active):
            try:
                await _butler_tick(ctx)
            except Exception:  # noqa: BLE001 — a missed reminder is retried a minute later
                log.warning("butler loop failed", exc_info=True)
        await asyncio.sleep(60)


async def _productivity_loop(ctx) -> None:
    """Alarms ring with a rising chime and a short brief; the plan, wind-down and summary go out once a day, each
    only if its own switch is on (never while locked down)."""
    provider = ctx.services.get("productivity")
    if provider is None:
        return
    from minty_core.productivity.runner import DayRunner, alarm_chime

    async def chime(volume: float) -> None:
        pipeline = ctx.pipeline
        if pipeline is not None and pipeline.ready.is_set():
            await pipeline.player.play(alarm_chime(volume))

    runner = DayRunner(provider, lambda text: ctx.bus.publish("notify", text=text),
                       lambda text: ctx.bus.publish("notify", text=text, urgent=True), chime)
    while True:
        lock = ctx.services.get("lockdown")
        if not (lock is not None and lock.active):
            try:
                await runner.tick()
            except Exception:  # noqa: BLE001 — retried on the next pass
                log.warning("productivity loop failed", exc_info=True)
        await asyncio.sleep(30)


async def _selfcheck_loop(ctx) -> None:
    """The daily tip, the Sunday self-report and the evening card, each only if its own switch is on."""
    provider = ctx.services.get("selfcheck")
    if provider is None:
        return
    while True:
        lock = ctx.services.get("lockdown")
        if not (lock is not None and lock.active):
            try:
                for text in await provider.tick():
                    ctx.bus.publish("notify", text=text)
            except Exception:  # noqa: BLE001 — retried on the next pass
                log.warning("selfcheck loop failed", exc_info=True)
        await asyncio.sleep(120)


async def _integrations_loop(ctx) -> None:
    """Calendar feeds refreshed hourly and new weather alerts said once (never while locked down)."""
    provider = ctx.services.get("integrations")
    if provider is None:
        return
    while True:
        lock = ctx.services.get("lockdown")
        if not (lock is not None and lock.active):
            try:
                for text in await provider.tick():
                    ctx.bus.publish("notify", text=text)
            except Exception:  # noqa: BLE001 — retried on the next pass
                log.warning("integrations loop failed", exc_info=True)
        await asyncio.sleep(120)


async def _create_loop(ctx) -> None:
    """The daily news brief and new arXiv papers, each only if its own switch is on (never while locked down)."""
    provider = ctx.services.get("create")
    if provider is None:
        return
    while True:
        lock = ctx.services.get("lockdown")
        if not (lock is not None and lock.active):
            try:
                for text in await provider.tick():
                    ctx.bus.publish("notify", text=text)
            except Exception:  # noqa: BLE001 — retried on the next pass
                log.warning("create loop failed", exc_info=True)
        await asyncio.sleep(60)


async def _dev_loop(ctx) -> None:
    """CI on the projects you watch: said when a workflow turns red or green again, never asked faster than dev.ci_minutes."""
    provider = ctx.services.get("dev")
    if provider is None:
        return
    while True:
        lock = ctx.services.get("lockdown")
        if not (lock is not None and lock.active):
            try:
                for text in await provider.tick():
                    ctx.bus.publish("notify", text=text)
            except Exception:  # noqa: BLE001 — retried on the next pass
                log.warning("dev loop failed", exc_info=True)
        await asyncio.sleep(60)


async def _system_loop(ctx) -> None:
    """Battery, heat and CPU-hog alerts, and the notification listener, each only while its own switch is on."""
    provider = ctx.services.get("system")
    if provider is None:
        return
    from minty_core.power import read_battery, read_temperature
    from minty_core.system.notify import monitor
    from minty_core.system.proc import top_cpu

    listener = None
    while True:
        cfg = ctx.store.config.system
        lock = ctx.services.get("lockdown")
        try:
            if cfg.alerts and not (lock is not None and lock.active):
                on_ac, pct = read_battery()
                rows = await asyncio.to_thread(provider.sampler().sample)
                hogs = [(p.name, c) for p, c in top_cpu(rows, 3, 1.0)]
                for text in provider.alerts().feed(battery=pct, on_ac=on_ac, temp=read_temperature(), hogs=hogs):
                    ctx.bus.publish("notify", text=text)
            wanted = cfg.notifications and bool(cfg.notification_apps)
            if wanted and (listener is None or listener.done()):
                listener = asyncio.create_task(monitor(provider.reader()))
                listener.add_done_callback(lambda t: _log_task_failure(t, "notification listener"))
            elif not wanted and listener is not None:
                listener.cancel()
                listener = None
        except Exception:  # noqa: BLE001 — retried on the next pass
            log.warning("system loop failed", exc_info=True)
        await asyncio.sleep(30)


async def _web_loop(ctx) -> None:
    """Watchers: price, stock and page checks that have come due, each at its own polite pace; a change is said once
    (never while locked down)."""
    provider = ctx.services.get("web")
    if provider is None:
        return
    while True:
        lock = ctx.services.get("lockdown")
        if not (lock is not None and lock.active):
            try:
                for text in await provider.tick():
                    ctx.bus.publish("notify", text=text)
            except Exception:  # noqa: BLE001 — retried on the next pass
                log.warning("web loop failed", exc_info=True)
        await asyncio.sleep(60)


async def _study_loop(ctx) -> None:
    """Class reminders, tomorrow's bag and the Sunday review: each only if you switched it on, each said once."""
    provider = ctx.services.get("study")
    if provider is None:
        return
    from datetime import date as _date
    import time as _time

    from minty_core.inbox import Inbox
    from minty_core.study.kit import due_notices
    from minty_core.study.practice import weekly_review

    state: dict = {}

    def review() -> str:
        k = provider.kit()
        inbox = Inbox(ctx.config_dir / "inbox.db")
        try:
            open_ = inbox.open_homework()
        finally:
            inbox.close()
        receipts = ctx.services.get("receipts")
        rows = receipts.since(_time.time() - 7 * 86400) if receipts is not None else []
        return weekly_review(k.cards, k.papers, k.mistakes, k.exams(), open_, rows, _date.today(), _time.time())

    while True:
        try:
            notices, state = due_notices(provider, datetime.now(), state, review)
            for text, urgent in notices:
                ctx.bus.publish("notify", text=text, urgent=True) if urgent else ctx.bus.publish("notify", text=text)
        except Exception:  # noqa: BLE001 — a missed reminder is not worth a crash
            log.warning("study loop failed", exc_info=True)
        await asyncio.sleep(60)


async def _learned_loop(ctx) -> None:
    """Sunday evening, once: what I learned about you this week, so you can correct or drop it (off by default)."""
    from minty_core.governance.audit import MemoryAudit
    from minty_core.governance.learned import learned_notice

    last = ""
    while True:
        try:
            audit = MemoryAudit(ctx.memory, ctx.services.get("gists"), ctx.services.get("vault"))
            text, last = learned_notice(audit, datetime.now(), last, ctx.store.config.memory.weekly_learned)
            if text:
                ctx.bus.publish("notify", text=text)
        except Exception:  # noqa: BLE001
            log.warning("weekly learned notice failed", exc_info=True)
        await asyncio.sleep(600)


async def _update_loop(ctx, directory: Path) -> None:
    """Once a day (update.check): is there a newer release on GitHub? Tells once on the display; installs only if update.auto_install."""
    from minty_core.selfupdate import background_check

    from minty_core.selfupdate import detect_repo

    await asyncio.sleep(90)  # not while everything is still starting
    root = Path(__file__).resolve().parents[2]
    while True:
        try:
            cfg = ctx.store.config.update
            if not cfg.repo:
                cfg = cfg.model_copy(update={"repo": await asyncio.to_thread(detect_repo, root)})  # a clone knows where it came from
            await background_check(cfg, directory, lambda kind, **f: ctx.bus.publish(kind, **f), root=root)
        except Exception:  # noqa: BLE001 - an update check must never trouble the assistant
            log.debug("update check failed", exc_info=True)
        await asyncio.sleep(3600)


async def _heartbeat_loop(ctx, directory: Path) -> None:
    """Touch the heartbeat file every minute; an 'alive' note at the chosen time; the weekly digest on Sunday."""
    from minty_core.heartbeat import Heartbeat, due_alive

    hb = Heartbeat(directory / "heartbeat.json")
    last_alive = last_digest = ""
    while True:
        hb.beat()
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        at = ctx.store.config.ops.heartbeat_time
        if due_alive(now, at, last_alive):
            last_alive = today
            ctx.bus.publish("notify", text="I'm alive and well.")
        if now.weekday() == 6 and now.hour >= 18 and last_digest != today:  # Sunday evening, on screen only
            last_digest = today
            try:
                ctx.bus.publish("display", title="Weekly health", content=_health_digest(ctx, directory))
            except Exception:  # noqa: BLE001
                log.warning("could not make the weekly digest", exc_info=True)
        await asyncio.sleep(60)


async def _attention_loop(ctx, *, first_s: float = 120.0, every_s: float = 600.0) -> None:
    """Every ten minutes, look for things that need the user (a blocked mission, an e-mail that may not have gone, an overdue
    follow-up). What is said goes through the scheduler's own gate, so night hours and the hourly limit still apply."""
    await asyncio.sleep(first_s)
    while True:
        att = ctx.services.get("attention")
        if att is not None and ctx.store.config.proactive.attention:
            try:
                att.tick()  # on the loop on purpose: it reads the job table and the scheduler, which the loop is changing
            except Exception:  # noqa: BLE001 — a look that fails is tried again next time
                log.warning("attention check failed", exc_info=True)
        await asyncio.sleep(every_s)


async def _gmail_loop(ctx, *, first_s: float = 180.0, rounds: int | None = None, sleep=asyncio.sleep) -> None:
    """When you turned it on, look for what is new in Gmail every few minutes (the watcher reads only what changed). Always keep the notifier
    moving: retry a delivery that failed, say an urgent notice nobody dealt with once more. A failing round is waited out inside the watcher."""
    await sleep(first_s)
    n = 0
    while rounds is None or n < rounds:
        n += 1
        cfg = ctx.store.config.gmail_watch
        watcher = ctx.services.get("gmail_watcher")
        if cfg.enabled and watcher is not None:
            try:
                await watcher.poll_once()
            except Exception:  # noqa: BLE001 — the watcher never raises; this is the last net
                log.warning("gmail check failed", exc_info=True)
        notifier = ctx.services.get("notifier")
        if notifier is not None:
            try:
                notifier.retry_due()
                notifier.remind_due()
                approvals, host = ctx.services.get("approvals"), getattr(ctx, "host", None)
                if approvals is not None and host is not None:  # once a week, only if there was a lot of asking and something that could go
                    from minty_core.friction import friction_note
                    friction_note(approvals, notifier, host.get_tool)
            except Exception:  # noqa: BLE001
                log.warning("notifier upkeep failed", exc_info=True)
        await sleep(cfg.interval_s if cfg.enabled else 300.0)


async def _watchdog_loop(ctx, *, every_s: float = 30.0, rounds: int | None = None, sleep=asyncio.sleep) -> None:
    """Look at everything running every half minute from outside the work: stalled or looping jobs, state that does not match, a workflow that keeps failing."""
    n = 0
    while rounds is None or n < rounds:
        n += 1
        watch = ctx.services.get("watchdog")
        if watch is not None:
            try:
                await watch.tick()
            except Exception:  # noqa: BLE001 — the watchdog must never be the thing that stops the app
                log.warning("watchdog round failed", exc_info=True)
        await sleep(every_s)


async def _composio_apps_loop(ctx, *, first_s: float = 90.0, rounds: int | None = None, sleep=asyncio.sleep) -> None:
    """Keep the list of apps connected through Composio up to date by itself (read-only): a minute after start, then twice a day, so "upload
    this to YouTube" is understood without anyone explaining that Composio is connected to it."""
    await sleep(first_s)
    n = 0
    while rounds is None or n < rounds:
        n += 1
        refresh = ctx.services.get("composio_apps_refresh")
        heard = 0
        if refresh is not None and ctx.store.config.mcp.enabled:
            try:
                heard = await refresh()
            except Exception:  # noqa: BLE001 — never a reason to stop the app
                log.warning("could not look through the connected apps", exc_info=True)
        await sleep(12 * 3600.0 if heard else 3600.0)


async def _recipes_loop(ctx, *, every_s: float = 60.0, rounds: int | None = None) -> None:
    """Every minute, start the recipes whose time has come (each is a mission with all its checks). On the loop, like the
    attention check, because it reads the job table."""
    n = 0
    while rounds is None or n < rounds:
        n += 1
        flows = ctx.services.get("flows")
        if flows is not None and not getattr(ctx.services.get("lockdown"), "active", False):
            try:
                flows.tick()  # the workflows whose schedule has come
            except Exception:  # noqa: BLE001 — tried again next minute
                log.warning("workflow schedule check failed", exc_info=True)
        recipes = ctx.services.get("recipes")
        if recipes is not None:
            lock = ctx.services.get("lockdown")
            try:
                recipes.tick(halted=bool(getattr(lock, "active", False)))
            except Exception:  # noqa: BLE001 — a failing look is tried again next minute
                log.warning("recipe check failed", exc_info=True)
        await asyncio.sleep(every_s)


async def _leak_loop(ctx, directory: Path) -> None:
    """Every five minutes: memory, open files, threads, database logs. A growth trend is said once a day."""
    from minty_core import leakguard

    guard = leakguard.LeakGuard()
    ctx.services["leakguard"] = guard
    told: dict[str, str] = {}
    while True:
        await asyncio.sleep(300)
        queue = len(ctx.services["jobs"].running())
        guard.add(leakguard.take(directory, queue=queue))
        today = datetime.now().strftime("%Y-%m-%d")
        for alarm in guard.alarms():
            key = alarm.split(":")[0]
            if told.get(key) != today:
                told[key] = today
                log.warning("leak guard: %s", alarm)
                ctx.bus.publish("notify", text=f"Something looks wrong: {alarm}. I'd restart me when it suits you.")


def on_resume(ctx, slept_s: float) -> None:
    """The computer woke from sleep: look at the network again, open the audio devices again, say so."""
    log.info("resumed after about %.0f s", slept_s)
    ctx.services["offline"].probe.note_failure()  # the network may have changed while asleep
    sup = ctx.services.get("voice_supervisor")
    if sup is not None:
        sup.restart()  # the audio devices are opened again
    ctx.bus.publish("notify", text="I'm back after the computer slept. I've reopened the microphone.")


async def _power_loop(ctx) -> None:
    """Every few seconds: notice a resume from sleep (re-open audio, re-probe the network), heat and battery."""
    from minty_core.power import PowerMonitor

    monitor = PowerMonitor()
    ctx.services["power"] = monitor
    warned_low = False
    while True:
        await asyncio.sleep(5)
        slept = monitor.check_resume()
        if slept is not None:
            on_resume(ctx, slept)
        state = monitor.sample()
        if state.low_battery and not warned_low and not ctx.store.config.system.alerts:
            warned_low = True
            ctx.bus.publish("notify", text=f"The battery is at {state.battery} percent and not charging.")
        elif not state.low_battery:
            warned_low = False


def plugin_maintenance(ctx):
    """One maintenance task that runs whatever plugins registered in `ctx.services["maintenance_tasks"]`. Looked up each day, so a
    plugin that was reloaded since start-up is the one that runs; one that fails is named and does not stop the others."""
    async def run() -> str:
        lock = ctx.services.get("lockdown")
        if lock is not None and lock.active:  # a lockdown stops what runs by itself, downloads included
            return "skipped (lockdown is on)"
        done = []
        for name, task in list((ctx.services.get("maintenance_tasks") or {}).items()):
            try:
                out = task()
                if asyncio.iscoroutine(out):
                    out = await out
                done.append(f"{name}: {out}")
            except Exception as e:  # noqa: BLE001
                log.warning("maintenance task of plugin %s failed", name, exc_info=True)
                done.append(f"{name}: failed ({type(e).__name__})")
        return "; ".join(done) or "nothing registered"

    return run


async def _maintenance_loop(ctx, directory: Path) -> None:
    """Once a day in a quiet window: trim the disk, compact the databases, anything else registered."""
    from minty_core.housekeeping import DiskGuard, MaintenanceWindow, maintain_databases

    cfg = ctx.store.config.ops
    guard = DiskGuard(directory, free_floor_mb=cfg.disk_free_floor_mb, max_config_mb=cfg.disk_max_config_mb)
    pipeline = ctx.pipeline
    window = MaintenanceWindow(directory / "maintenance.last", at=cfg.maintenance_time,
                               busy_for=(pipeline.idle_for if pipeline is not None else (lambda: 1e9)))
    window.tasks["disk"] = lambda: guard.trim() or "nothing to trim"
    window.tasks["databases"] = lambda: maintain_databases(directory)

    def backup_task() -> str:
        from minty_core import backup as backups

        cfg_now = ctx.store.config.ops
        if not cfg_now.backup:
            return "off"
        folder = Path(cfg_now.backup_folder).expanduser() if cfg_now.backup_folder else directory / "backups"
        keyfile = directory / "backup.key"
        first = not keyfile.exists()
        try:
            key = backups.load_or_make_keyfile(keyfile)
            path = folder / f"minty-backup-{datetime.now():%Y%m%d}.bak"
            info = backups.create(directory, key, path, include_raw=cfg_now.backup_include_raw)
            result = backups.verify(path, key)
            backups.prune(folder)
        except backups.BackupError as e:
            return f"skipped: {e}"
        if first:
            ctx.bus.publish("notify", text=f"I made a backup key at {keyfile}. Copy it somewhere safe: without it a "
                                           "backup cannot be opened.")
        verdict = "restore test passed" if result.ok else "restore test FAILED: " + "; ".join(result.problems)
        return f"{info.files} files, {info.size // 1024} KB, {verdict}"

    window.tasks["backup"] = backup_task
    ctx.services["backup_now"] = backup_task

    async def canary_task() -> str:
        from minty_core.canary import run_canary

        brownout = ctx.services.get("brownout")
        report = await run_canary(ctx.router, share=lambda: brownout.capacity().share if brownout else None,
                                  state_path=directory / "canary.json")
        ctx.services["canary_last"] = report
        return report.words()

    window.tasks["canary"] = canary_task
    window.tasks["plugins"] = plugin_maintenance(ctx)
    ctx.services["canary_now"] = canary_task
    ctx.services["maintenance"] = window
    while True:
        await asyncio.sleep(60)
        if not ctx.store.config.ops.maintenance:
            continue
        power = ctx.services.get("power")
        if power is not None and not power.state.heavy_work_ok():
            continue  # hot or nearly out of battery: housekeeping can wait
        system = ctx.services.get("system")
        if system is not None and ctx.store.config.system.resource_gate and not system.gate().check().ok:
            continue  # busy, short of memory or on a low battery: housekeeping can wait
        try:
            results = await window.run()
            if results:
                log.info("maintenance: %s", results)
        except Exception:  # noqa: BLE001
            log.warning("maintenance pass failed", exc_info=True)


def _start_tui(ctx, directory: Path):
    """Run the full-screen display; log lines go to a file so they don't tear the screen."""
    from minty_core.logredact import PrivateRotatingFileHandler
    from minty_core.tui import Hotkeys, TerminalUI
    from minty_core.typed import typed_chat

    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            root.removeHandler(handler)
    log_file = PrivateRotatingFileHandler(directory / "minty.log", maxBytes=1_000_000, backupCount=2)
    log_file.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    root.addHandler(log_file)

    from minty_core.statusline import snapshot

    def status():
        try:
            return snapshot(ctx)
        except Exception:  # a display must never take the assistant down
            log.debug("status snapshot failed", exc_info=True)
            return {}

    task = asyncio.create_task(TerminalUI(
        ctx.bus, status=status, reduced_motion=lambda: ctx.store.config.terminal_reduced_motion,
        images=lambda: ctx.store.config.terminal_images,
        cover_size=lambda: ctx.store.config.terminal_cover_size,
        on_submit=lambda text: typed_chat(ctx, text), on_action=_button_action(ctx),
        hotkeys=lambda: Hotkeys.from_config(ctx.store.config), mouse=lambda: ctx.store.config.terminal_mouse).run())
    task.add_done_callback(lambda t: _log_task_failure(t, "terminal display"))
    return task


def _build_panel_server(directory: Path):
    import contextlib

    import uvicorn
    from minty_panel.main import build_panel_app

    class PanelServer(uvicorn.Server):
        def capture_signals(self):  # the core's server owns Ctrl+C; we are told to stop explicitly
            return contextlib.nullcontext()

    args = build_panel_app(directory)
    return PanelServer(uvicorn.Config(args["app"], host=args["host"], port=args["port"],
                                      log_level="warning", proxy_headers=False))


_restart = {"requested": False, "server": None, "lock": None}  # lock: the descriptor that keeps this the only Minty here


def request_restart() -> None:
    """Ask the running process to shut down cleanly and start itself again (picks up code changes)."""
    _restart["requested"] = True
    server = _restart["server"]
    if server is not None:
        server.should_exit = True


def _reexec() -> None:
    """Replace this process with a fresh one running the same command."""
    import atexit

    atexit._run_exitfuncs()  # stops coding-agent jobs the same way a normal exit does
    logging.shutdown()
    os.execv(sys.executable, [sys.executable, *_without_safe_flag(sys.argv)])


def _without_safe_flag(argv: list[str]) -> list[str]:
    """A restart is how you leave safe mode, so it must not carry the flag that asked for it."""
    return [a for a in argv if a != "--safe"]


def preload_voice_models(store) -> float:
    """Load (and warm up) every voice model so the first request isn't slow. Blocking; run in a thread."""
    import numpy as np

    started = time.monotonic()
    cfg = store.config
    try:
        from minty_core.audio.stt import _load_whisper

        model = _load_whisper(effective_local_model(cfg.stt))
        list(model.transcribe(np.zeros(16000, dtype=np.float32), language="en", beam_size=1)[0])
    except Exception as e:
        log.warning("could not preload speech recognition: %s", e)
    try:
        from minty_core.audio.tts import ConfiguredTTS

        ConfiguredTTS(store).synthesize_sync("ready")
    except Exception as e:
        log.warning("could not preload the voice: %s", e)
    try:
        if cfg.audio.wake_mode == "model":
            from minty_core.audio.devices import _load_oww

            _load_oww(cfg.audio.wake_word)
    except Exception as e:
        log.warning("could not preload the wake word model: %s", e)
    return time.monotonic() - started


def voice_libraries_missing() -> str:
    """The first voice library that is not installed, or "" when the voice can run. The Docker core image has none on purpose."""
    import importlib.util

    for name in ("sounddevice",):
        if importlib.util.find_spec(name) is None:
            return name
    return ""


async def _start_voice(ctx: AppContext) -> None:
    pipeline = ctx.pipeline
    missing = voice_libraries_missing()
    if missing:  # text, the panel, Telegram and the tools do not need a microphone: start without the voice, quietly
        log.info("voice is off: %s is not installed (text, the panel and Telegram still work; for voice: pip install 'minty-core[voice]')",
                 missing)
        pipeline.ready.set()
        return
    pipeline._set_state("loading")
    seconds = await asyncio.to_thread(preload_voice_models, ctx.store)
    log.info("voice models ready in %.1fs", seconds)
    await pipeline.warm_up()  # "Yes, sir?" and the stock phrases are synthesised once now
    from minty_core.voice.ptt import PushToTalk, PynputHotkey

    ptt = PushToTalk(lambda: ctx.store.config.audio.push_to_talk)
    pipeline.ptt = ptt
    ctx.services["ptt"] = ptt
    hotkey = PynputHotkey(ctx.store.config.audio.ptt_key, ptt, asyncio.get_running_loop())
    if ctx.store.config.audio.push_to_talk and not hotkey.start():
        ctx.bus.publish("notice", message="Push-to-talk is on but no key listener is available (pip install "
                                          "pynput); press the panel button or use the control API instead.")
    ctx.services["ptt_hotkey"] = hotkey
    prod = ctx.services.get("productivity")
    if prod is not None:
        # the recorder gets frames only between an explicit start and stop; while it runs, non-urgent notices wait
        from minty_core.productivity.recorder import Holds

        recorder = prod.recorder()
        pipeline.recorder = ctx.services["recorder"] = recorder
        ctx.services["transcribe"] = pipeline.stt.transcribe
        pipeline.focus = Holds(ctx.services.get("focus"), recorder)
    else:
        pipeline.focus = ctx.services.get("focus")
    if ctx.store.config.audio.aec:
        from minty_core.voice.aec import EchoCancel

        if not EchoCancel().apply_environment():
            ctx.bus.publish("notice", message="audio.aec is on but the echo-cancel module is not loaded; run: "
                                              "minty aec on")
    pipeline._set_state("idle")
    pipeline.ready.set()
    # the voice runs under a watchdog: if the audio side dies it restarts alone, the core keeps going
    supervisor = VoiceSupervisor(pipeline.run, notify=lambda text: ctx.bus.publish("notify", text=text))
    ctx.services["voice_supervisor"] = supervisor
    await supervisor.run()


VOICE_FIRST_S = 20.0  # the control API and the panel start once the voice is ready, or after this long


async def wait_for_voice(ctx, timeout: float = VOICE_FIRST_S) -> bool:
    """Let the voice load first (the web API's import alone costs ~0.27 s of CPU); False if it did not get ready."""
    ready = getattr(getattr(ctx, "pipeline", None), "ready", None)
    if ready is None:
        return False
    try:
        await asyncio.wait_for(ready.wait(), timeout)
        return True
    except TimeoutError:
        return False


async def startup_checks(ctx: AppContext) -> dict[str, str]:
    """The self-check line now, then again with the key probe's findings if anything is wrong."""
    from minty_core.statusline import selfcheck_line

    line = selfcheck_line(ctx)
    log.info(line)
    ctx.bus.publish("notice", message=line)
    try:
        found = await ctx.router.probe()
    except Exception:  # noqa: BLE001 — a failed check must never stop the start
        log.warning("start-up key probe failed", exc_info=True)
        return {}
    if any(state != "ok" for state in found.values()):
        line = selfcheck_line(ctx, probe=found)
        log.warning(line)
        ctx.bus.publish("notice", message=line)
    return found


def announce_interrupted(ctx: AppContext) -> str:
    """Honest restart: say which background work was cut short. Nothing is re-run on its own."""
    notice = interrupted_notice(ctx.services["jobs"].interrupted,
                                {j.id for j in ctx.services["jobs"].resumable()})
    if ctx.services.get("safe_mode"):
        why = "you asked for it" if ctx.services["safe_mode"] == "requested" else "I crashed several times in a row"
        notice = (f"I started in safe mode because {why}, with only the basics: "
                  "clock, timers, memory, notes and search. Say leave safe mode to restart normally. " + notice).strip()
    lock = ctx.services.get("lockdown")
    if lock is not None and lock.active:
        notice = ("I'm still in lockdown, so nothing changes or goes out. Say end lockdown to lift it. " + notice).strip()
    if notice:
        ctx.bus.publish("notify", text=notice)
    return notice


async def _run(directory: Path, with_panel: bool = True, safe: bool = False) -> None:
    ctx = build_context(directory)
    logging.getLogger().addHandler(ctx.logs)
    ctx.bus.attach_loop(asyncio.get_running_loop())
    boot = BootLog(directory / "boot.json")
    ctx.services["bootlog"] = boot
    crash_loop = boot.start()  # three starts in ten minutes without a clean exit
    safe = safe or boot.take_safe_request()  # "enter safe mode" by voice asks for one safe start
    if start_in_safe_mode(crash_loop=crash_loop, auto=ctx.store.config.ops.auto_safe_mode, requested=safe):
        ctx.host.only = set(SAFE_PLUGINS)
        ctx.services["safe_mode"] = "requested" if safe else "crash loop"
        log.warning("starting in safe mode (%s) with %s", ctx.services["safe_mode"], ", ".join(SAFE_PLUGINS))
    elif crash_loop:
        log.info("several starts without a clean stop; safe mode is off (ops.auto_safe_mode), starting normally")
    voice_task = None
    panel_server = panel_task = None
    try:
        ctx.pipeline = build_voice_pipeline(ctx)
        voice_task = asyncio.create_task(_start_voice(ctx))
        voice_task.add_done_callback(lambda t: _log_task_failure(t, "voice pipeline"))
        voice_task.add_done_callback(lambda t: ctx.pipeline.ready.set())  # a failed voice never holds up the API
    except Exception:
        log.exception("voice pipeline unavailable; running the control API only")
    announce_interrupted(ctx)
    from minty_core.runtime import tune_gc

    log.info("runtime tuned: %s", tune_gc())  # everything loaded so far is steady: the collector need not rescan it
    from minty_core.wizard import needs_setup

    if needs_setup(directory):
        ctx.bus.publish("notify", text="This looks like a first start. In a terminal, run: minty setup")
    checks_task = asyncio.create_task(startup_checks(ctx))
    checks_task.add_done_callback(lambda t: _log_task_failure(t, "start-up checks"))
    compact_task = asyncio.create_task(_compaction_loop(ctx))
    compact_task.add_done_callback(lambda t: _log_task_failure(t, "memory compaction"))
    leak_task = asyncio.create_task(_leak_loop(ctx, directory))
    leak_task.add_done_callback(lambda t: _log_task_failure(t, "leak guard"))
    power_task = asyncio.create_task(_power_loop(ctx))
    power_task.add_done_callback(lambda t: _log_task_failure(t, "power monitor"))
    feed = ctx.services.get("overlay_feed")
    feed_task = asyncio.create_task(feed.run()) if feed is not None else None
    focus_task = asyncio.create_task(_focus_loop(ctx))
    focus_task.add_done_callback(lambda t: _log_task_failure(t, "focus guard"))
    timers_task = asyncio.create_task(_timers_loop(ctx))
    timers_task.add_done_callback(lambda t: _log_task_failure(t, "timers"))
    narration_task = asyncio.create_task(_narration_loop(ctx))
    narration_task.add_done_callback(lambda t: _log_task_failure(t, "progress narration"))
    life_task = asyncio.create_task(_life_loop(ctx))
    life_task.add_done_callback(lambda t: _log_task_failure(t, "life"))
    vault_task = asyncio.create_task(_vault_loop(ctx))
    vault_task.add_done_callback(lambda t: _log_task_failure(t, "vault"))
    butler_task = asyncio.create_task(_butler_loop(ctx))
    butler_task.add_done_callback(lambda t: _log_task_failure(t, "butler"))
    productivity_task = asyncio.create_task(_productivity_loop(ctx))
    productivity_task.add_done_callback(lambda t: _log_task_failure(t, "productivity"))
    web_task = asyncio.create_task(_web_loop(ctx))
    web_task.add_done_callback(lambda t: _log_task_failure(t, "web"))
    system_task = asyncio.create_task(_system_loop(ctx))
    system_task.add_done_callback(lambda t: _log_task_failure(t, "system"))
    dev_task = asyncio.create_task(_dev_loop(ctx))
    dev_task.add_done_callback(lambda t: _log_task_failure(t, "dev"))
    create_task = asyncio.create_task(_create_loop(ctx))
    create_task.add_done_callback(lambda t: _log_task_failure(t, "create"))
    integrations_task = asyncio.create_task(_integrations_loop(ctx))
    integrations_task.add_done_callback(lambda t: _log_task_failure(t, "integrations"))
    selfcheck_task = asyncio.create_task(_selfcheck_loop(ctx))
    selfcheck_task.add_done_callback(lambda t: _log_task_failure(t, "selfcheck"))
    learned_task = asyncio.create_task(_learned_loop(ctx))
    learned_task.add_done_callback(lambda t: _log_task_failure(t, "weekly learned"))
    study_task = asyncio.create_task(_study_loop(ctx))
    study_task.add_done_callback(lambda t: _log_task_failure(t, "study"))
    finance_task = asyncio.create_task(_finance_loop(ctx, directory))
    finance_task.add_done_callback(lambda t: _log_task_failure(t, "finance reminders"))
    coach_task = asyncio.create_task(_coach_loop(ctx))
    coach_task.add_done_callback(lambda t: _log_task_failure(t, "coach"))
    health_task = asyncio.create_task(_health_loop(ctx))
    health_task.add_done_callback(lambda t: _log_task_failure(t, "health sync"))
    telegram_task = asyncio.create_task(_telegram_loop(ctx))
    telegram_task.add_done_callback(lambda t: _log_task_failure(t, "telegram"))
    update_task = asyncio.create_task(_update_loop(ctx, directory))
    update_task.add_done_callback(lambda t: _log_task_failure(t, "update check"))
    heart_task = asyncio.create_task(_heartbeat_loop(ctx, directory))
    heart_task.add_done_callback(lambda t: _log_task_failure(t, "heartbeat"))
    attention_task = asyncio.create_task(_attention_loop(ctx))
    attention_task.add_done_callback(lambda t: _log_task_failure(t, "attention"))
    gmail_task = asyncio.create_task(_gmail_loop(ctx))
    gmail_task.add_done_callback(lambda t: _log_task_failure(t, "gmail"))
    watchdog_task = asyncio.create_task(_watchdog_loop(ctx))
    watchdog_task.add_done_callback(lambda t: _log_task_failure(t, "watchdog"))
    apps_task = asyncio.create_task(_composio_apps_loop(ctx))
    apps_task.add_done_callback(lambda t: _log_task_failure(t, "composio apps"))
    recipes_task = asyncio.create_task(_recipes_loop(ctx))
    recipes_task.add_done_callback(lambda t: _log_task_failure(t, "recipes"))
    maintain_task = asyncio.create_task(_maintenance_loop(ctx, directory))
    maintain_task.add_done_callback(lambda t: _log_task_failure(t, "maintenance"))
    from minty_core.loopwatch import watch as watch_loop

    loop_task = asyncio.create_task(watch_loop(ctx.services["tracer"]))
    schedule_task = asyncio.create_task(ctx.services["scheduler"].run())
    schedule_task.add_done_callback(lambda t: _log_task_failure(t, "scheduler"))
    ctx.services["restart"] = request_restart
    tui_task = None
    if ctx.store.config.terminal_ui and sys.stdout.isatty():
        tui_task = _start_tui(ctx, directory)
    try:
        await wait_for_voice(ctx)
        import uvicorn

        from minty_core.api import create_app

        server = uvicorn.Server(uvicorn.Config(create_app(ctx), host="127.0.0.1", port=ctx.store.config.api_port,
                                               log_level="info"))
        _restart["server"] = server
        if with_panel:
            try:
                panel_server = _build_panel_server(directory)
                panel_task = asyncio.create_task(panel_server.serve())
                panel_task.add_done_callback(lambda t: _log_task_failure(t, "panel"))
                log.info("panel on http://%s:%s", panel_server.config.host, panel_server.config.port)
            except Exception:
                log.exception("panel unavailable; the core keeps running")
        await server.serve()
    finally:
        if is_deliberate_stop(sys.exc_info()[1]):
            boot.clean_exit()  # at once: a second Ctrl+C during the slow clean-up must not count as a crash
        compact_task.cancel()
        telegram_task.cancel()
        update_task.cancel()
        health_task.cancel()
        coach_task.cancel()
        finance_task.cancel()
        life_task.cancel()
        vault_task.cancel()
        butler_task.cancel()
        productivity_task.cancel()
        web_task.cancel()
        system_task.cancel()
        dev_task.cancel()
        create_task.cancel()
        integrations_task.cancel()
        selfcheck_task.cancel()
        learned_task.cancel()
        study_task.cancel()
        narration_task.cancel()
        timers_task.cancel()
        focus_task.cancel()
        if feed_task is not None:
            feed_task.cancel()
        maintain_task.cancel()
        heart_task.cancel()
        attention_task.cancel()
        gmail_task.cancel()
        apps_task.cancel()
        watchdog_task.cancel()
        recipes_task.cancel()
        power_task.cancel()
        leak_task.cancel()
        schedule_task.cancel()
        loop_task.cancel()
        checks_task.cancel()
        await asyncio.gather(compact_task, telegram_task, health_task, coach_task, finance_task, life_task,
                             vault_task, butler_task, productivity_task, web_task, system_task, dev_task, create_task, integrations_task, selfcheck_task, learned_task, study_task, narration_task, timers_task, focus_task, maintain_task, heart_task, attention_task, gmail_task, apps_task, watchdog_task, recipes_task, power_task, leak_task, schedule_task,
                             loop_task, checks_task, return_exceptions=True)
        if tui_task is not None:
            tui_task.cancel()
            await asyncio.gather(tui_task, return_exceptions=True)
        if panel_server is not None:
            panel_server.should_exit = True
            await asyncio.gather(panel_task, return_exceptions=True)
        if voice_task is not None:
            voice_task.cancel()
            await asyncio.gather(voice_task, return_exceptions=True)
        await ctx.services["jobs"].shutdown()  # running jobs are recorded as interrupted, reported next start
        boot.clean_exit()  # reached only on a normal stop: a crash leaves the start marked unclean
        ctx.services["journal"].mark_clean()


_MAX_KEY_FILE = 1 << 20


def _read_keys_file(path: str) -> str:
    try:
        # O_NONBLOCK so opening a FIFO cannot hang; only regular files are read.
        fd = os.open(Path(path).expanduser(), os.O_RDONLY | os.O_NONBLOCK)
    except OSError as e:
        sys.exit(f"minty-core: cannot read {path}: {e.strerror or type(e).__name__}")
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        sys.exit(f"minty-core: {path} is not a regular file")
    with os.fdopen(fd, "rb") as f:
        try:
            raw = f.read(_MAX_KEY_FILE + 1)
        except OSError as e:
            sys.exit(f"minty-core: cannot read {path}: {e.strerror or type(e).__name__}")
    if len(raw) > _MAX_KEY_FILE:
        sys.exit(f"minty-core: {path}: file too large for a keys file")
    return raw.decode("utf-8", errors="replace")


def _import_keys(path: str, directory: Path) -> None:
    keys = extract_keys(_read_keys_file(path))
    if not keys:
        sys.exit(f"minty-core: no API keys found in {path}")
    store = SecretsStore(directory)
    try:
        existing = set(store.names())
        store.set_many(keys)
    except (tomllib.TOMLDecodeError, SecretsDamaged):
        sys.exit("secrets.toml is not valid TOML; fix or delete it")
    print(f"Imported {len(keys)} {'key' if len(keys) == 1 else 'keys'}: {', '.join(sorted(keys))}")
    replaced = sorted(existing & keys.keys())
    if replaced:
        print(f"Replaced existing: {', '.join(replaced)}")
    print(f"Now move or delete {path} — it holds your keys in plain text.")


def _notes_lock_command(action: str, store) -> int:
    """Unlock or lock the private notes in the running Minty. The passphrase goes to this machine's own control
    API (localhost, with the token) and nowhere else: never to a model, never to Telegram."""
    import httpx

    directory = config_dir()
    token = (directory / "api_token").read_text().strip() if (directory / "api_token").exists() else ""
    base = f"http://127.0.0.1:{store.config.api_port}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        if action == "lock":
            httpx.post(f"{base}/vault/private/lock", headers=headers, timeout=5).raise_for_status()
            print("The private notes are locked.")
            return 0
        passphrase = getpass.getpass("Passphrase (or recovery phrase): ")
        res = httpx.post(f"{base}/vault/private/unlock", headers=headers, json={"passphrase": passphrase}, timeout=10)
        if res.status_code == 200:
            print(f"The private notes are unlocked for {store.config.vault.private_lock_minutes} idle minutes.")
            return 0
        print(res.json().get("detail", f"Could not unlock (HTTP {res.status_code})."))
    except (httpx.HTTPError, ValueError) as e:
        print(f"Minty is not running here, or did not answer ({type(e).__name__}). Start it first.")
    return 1


def _run_overlay() -> None:
    """The HUD process: poll the control API, follow the active window, draw a few labels."""
    import httpx

    from minty_core.overlay.model import OverlayModel
    from minty_core.overlay.window import HudWindow
    from minty_core.overlay.xwin import ActiveWindowReader

    directory = config_dir()
    store = ConfigStore(directory)
    cfg = store.load()
    token = (directory / "api_token").read_text().strip() if (directory / "api_token").exists() else ""
    reader = ActiveWindowReader()
    if not reader.available():
        sys.exit("minty overlay: it needs an X11 session with xprop (Wayland has no active-window query).")
    client = httpx.Client(base_url=f"http://127.0.0.1:{cfg.api_port}", timeout=0.4,
                          headers={"Authorization": f"Bearer {token}"})
    model = OverlayModel(enabled=cfg.overlay.hud, hints=tuple(cfg.overlay.hints))
    last = {"at": 0.0}
    import time

    def fetch():
        if time.monotonic() - last["at"] > 5:  # re-read the settings now and then (the HUD can be switched off)
            last["at"] = time.monotonic()
            model.enabled = store.load().overlay.hud
        return client.get("/overlay").json()

    HudWindow(model, reader, fetch, corner=cfg.overlay.corner, opacity=cfg.overlay.opacity).run()


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    install_uvicorn_redaction()  # request lines never carry a secret-looking query value into the console, journal or log file
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)  # one line per transcription otherwise
    for noisy in ("httpx", "httpcore"):  # they log full request URLs at INFO, which can carry query-auth keys
        logging.getLogger(noisy).setLevel(logging.WARNING)
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "set-key":
        prompt = f"API key for {args.name}: "
        value = (input(prompt) if args.show else getpass.getpass(prompt)).strip()
        if not value:
            sys.exit("empty key, nothing saved")
        try:
            SecretsStore(config_dir()).set(args.name, value)
        except SecretsDamaged as e:
            sys.exit(str(e))
        print(f"saved {args.name}")
        return
    if args.command == "service-unit":
        from minty_core.service import unit_text

        print(unit_text(Path(args.root) if args.root else Path(__file__).resolve().parents[2]), end="")
        return
    if args.command == "overlay":
        _run_overlay()
        return
    if args.command == "transfer":
        from minty_core.governance.export import cli as transfer_cli
        from minty_core.vault.kit import vault_root

        store = ConfigStore(config_dir())
        cfg = store.load()
        workspace = Path(str((cfg.plugins.get("browser") or {}).get("folder") or "~/Minty-Workspace")).expanduser()
        sys.exit(transfer_cli(list(args.rest), config_dir(),
                              vault_root(cfg.vault.path) if cfg.vault.enabled else None, workspace))
    if args.command == "mcp":
        from minty_core.mcp.cli import run_cli as mcp_cli

        secrets = SecretsStore(config_dir())
        sys.exit(asyncio.run(mcp_cli(list(args.rest), config_dir(), secrets.get)))
    if args.command == "butler":
        from minty_core.butler.standing import run_cli as standing_cli

        rest = list(args.rest)
        if rest[:1] == ["standing"]:
            sys.exit(standing_cli(rest[1:], config_dir() / "standing.json"))
        print("usage: minty butler standing grant <tool> [--arg k=v] [--per-day N] [--days D] [--note text] | "
              "list | revoke <n>")
        sys.exit(1)
    if args.command == "notes":
        from minty_core.vault.cli import run as run_notes
        from minty_core.vault.kit import VaultProvider

        store = ConfigStore(config_dir())
        store.load()
        if args.rest[:1] in (["unlock"], ["lock"]):
            sys.exit(_notes_lock_command(args.rest[0], store))
        sys.exit(run_notes(args.rest, VaultProvider(config=lambda: store.config)))
    if args.command == "aec":
        from minty_core.voice.aec import SINK, SOURCE, EchoCancel

        aec = EchoCancel()
        if args.action == "status":
            st = aec.status()
            print("Echo cancellation is " + ("on" if st.loaded else "off") + ".")
            return
        result = aec.enable() if args.action == "on" else aec.disable()
        print(result.message)
        if args.action == "on" and result.ok:
            print(f"Minty will use {SOURCE} and {SINK} when audio.aec is switched on (Switches page); it does not "
                  "change your system's default devices. Use it with speakers, not headphones.")
        if not result.ok:
            sys.exit(1)
        return
    if args.command == "prompts-show":
        from minty_core import promptlib
        from minty_core.personality import BASE_RULES

        store = ConfigStore(config_dir())
        cfg = store.load().personality
        off = cfg.prompt_modules_off if cfg.prompt_library else [m.name for m in promptlib.MODULES]
        base = promptlib.TELEGRAM_RULES if args.channel == "telegram" and "telegram_rules" in promptlib.GATES \
            and "telegram_rules" not in off else BASE_RULES
        mods = promptlib.select(channel=args.channel, tier=args.tier, kits=args.kit, off=off)
        print(f"channel {args.channel}, tier {args.tier}; library {'on' if cfg.prompt_library else 'off'}")
        print(f"- core rules: {len(base)} characters")
        for m in mods:
            print(f"- {m.name} v{m.version}: {len(m.text)} of {m.budget} characters")
        print(f"- not active (no passed gate yet): "
              f"{', '.join(m.name for m in promptlib.MODULES if m.name not in promptlib.GATES) or 'none'}")
        print(f"total modules {sum(len(m.text) for m in mods)} characters; version {promptlib.version(mods, base)}")
        return
    if args.command == "claude-brain":
        from minty_core.claude_brain import claude_brain

        store = ConfigStore(config_dir())
        store.load()
        print(claude_brain(args.action, store))
        return
    if args.command == "telegram-pair":
        from minty_core.telegram.runtime import pairing_instructions

        print(asyncio.run(pairing_instructions(config_dir(), SecretsStore(config_dir()))))
        return
    if args.command == "telegram-passphrase":
        from minty_core.telegram.approvals import Passphrase
        from minty_core.telegram.runtime import PASSPHRASE_FILE

        first = getpass.getpass("Passphrase for class-3 approvals from Telegram (8+ characters): ")
        if len(first) < 8:
            sys.exit("minty-core: too short, nothing saved")
        if getpass.getpass("Again: ") != first:
            sys.exit("minty-core: they did not match, nothing saved")
        Passphrase(config_dir() / PASSPHRASE_FILE).set(first)
        print("Saved (as a hash). It only counts once telegram.class3_passphrase is on.")
        return
    if args.command == "local-llm-test":
        from minty_core.llm.local import LocalModel, adopt

        directory = config_dir()
        store = ConfigStore(directory)
        cfg = store.load().local_llm
        model = LocalModel(cfg.model_path, cfg.n_ctx)
        ok, why = model.available()
        if not ok:
            sys.exit(f"minty-core: the local model cannot run: {why}")
        adopted_now, verdict = adopt(model, directory / "local-llm.json", min_tps=cfg.min_tokens_per_s,
                                     max_ram_mb=cfg.max_ram_mb)
        print(("Adopted: " if adopted_now else "Not adopted: ") + verdict)
        if adopted_now and not cfg.enabled:
            print("Set local_llm.enabled = true in the panel's Switches page to use it.")
        return
    if args.command == "update":
        from minty_core.selfupdate import detect_repo, run_cli as update_run

        store = ConfigStore(config_dir())
        store.load()
        cfg, root = store.config.update, Path(__file__).resolve().parents[2]
        sys.exit(asyncio.run(update_run(list(args.rest), root=root, config_dir=config_dir(), repo=cfg.repo or detect_repo(root),
                                        verify_signed=cfg.verify_signed)))
    if args.command == "instant":
        from minty_core.instant.cli import run as instant_run

        sys.exit(instant_run(list(args.rest)))
    if args.command == "tune":
        from minty_core.tune import cli as tune_cli

        sys.exit(tune_cli(list(args.rest), config_dir()))
    if args.command in ("onboard", "setup"):
        from minty_core import onboard

        flags = (["--step", args.step] if getattr(args, "step", "") else []) + (["--again"] if getattr(args, "again", False) else [])
        if not onboard.main(flags):
            return
        args.command = "run"
    elif args.command == "run" and sys.stdin.isatty() and sys.stdout.isatty() and not os.environ.get("MINTY_NO_ONBOARD"):
        from minty_core.wizard import needs_setup

        if needs_setup(config_dir()):
            from minty_core import onboard

            print("This looks like the first start, so here is the guided setup (Ctrl+C stops it; MINTY_NO_ONBOARD=1 skips it).\n")
            if not onboard.main([]):
                return
    if args.command == "spotify-login":
        from minty_core.spotify_login import login

        try:
            login(SecretsStore(config_dir()))
        except RuntimeError as e:
            sys.exit(f"minty-core: {e}")
        return
    if args.command in ("set-pin", "set-password"):
        from minty_panel.main import main as panel_main

        panel_main(["set-pin"] + (["--allow-easy"] if getattr(args, "allow_easy", False) else []))
        return
    if args.command == "profile-import":
        from minty_core.profile import ProfileStore
        from minty_core.profile_import import format_report, run_import

        try:
            text = Path(args.path).expanduser().read_text(encoding="utf-8")
        except OSError as e:
            sys.exit(f"minty: cannot read {args.path}: {e.strerror}")
        report = run_import(text, ProfileStore(config_dir() / "profile" / "answers.json"), apply=args.apply)
        print(format_report(report, args.apply))
        return
    if args.command == "import-keys":
        _import_keys(args.path, config_dir())
        return
    try:
        _restart["lock"] = instance.acquire(config_dir())  # before anything loads: a second Minty says so and stops
    except instance.AlreadyRunning as e:
        sys.exit(str(e))
    try:
        _restart["requested"] = False
        try:
            asyncio.run(_run(config_dir(), with_panel=not getattr(args, "no_panel", False),
                             safe=getattr(args, "safe", False)))
        finally:
            os.close(_restart["lock"])  # let go before a restart starts the next one (and so a run in this same process can follow)
            _restart["lock"] = None
        if _restart["requested"]:
            _reexec()
    except ConfigError as e:
        sys.exit(f"minty-core: {e}")
