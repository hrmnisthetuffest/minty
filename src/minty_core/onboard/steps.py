"""The guided setup, one short step at a time (`minty onboard`).

Eight steps: check this computer, choose a brain, voice, you and your assistant, safety, your phone, skills and extras,
test and start. Every step can be skipped, run again later (`minty onboard --step voice`) and nothing is downloaded or
installed without asking first. Keys are typed hidden and go straight into the key store (mode 0600); they are never
printed. The steps only change settings through the config store, so the settings page shows exactly what was chosen.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from minty_core.claude_brain import claude_brain
from minty_core.config import CLAUDE_BRAIN, ConfigStore
from minty_core.fsutil import atomic_write_text
from minty_core.keystore import SecretsStore
from minty_core.memory import MemoryStore
from minty_core.onboard.env import VOICE_FILES, VOICE_SIZE, Env
from minty_core.onboard.ui import UI, Option
from minty_core.personality import DEFAULT_PERSONALITIES
from minty_core.system.osinfo import detect, hint_for

STEPS = (
    ("system", "Check this computer"),
    ("brain", "Choose a brain"),
    ("voice", "Voice"),
    ("you", "You and your assistant"),
    ("safety", "Safety"),
    ("phone", "Your phone"),
    ("extras", "Skills and extras"),
    ("finish", "Test and start"),
)
STEP_IDS = tuple(s for s, _ in STEPS)
TAGLINE = "Your voice assistant, on your own computer."

PROVIDERS = (  # key name, label, what it is good for, where to get a free key
    ("groq", "Groq", "fastest free tier, also does speech-to-text", "https://console.groq.com/keys"),
    ("gemini", "Google Gemini", "free tier, needed for Gemini Live voice", "https://aistudio.google.com/apikey"),
    ("openrouter", "OpenRouter", "many free models", "https://openrouter.ai/keys"),
    ("cerebras", "Cerebras", "very fast free tier", "https://cloud.cerebras.ai"),
    ("mistral", "Mistral", "free experiment tier", "https://console.mistral.ai/api-keys"),
    ("nvidia", "NVIDIA", "free credits for large models", "https://build.nvidia.com/settings/api-keys"),
)
PERSONA_HINTS = {"minty": "calm, dry and loyal (the default)", "jarvis": "polished and formal, like a butler",
                 "casual": "relaxed, like a friend"}
TONES = {"serious": (6, 1, 2), "balanced": (5, 3, 3), "chatty": (3, 7, 7)}  # formality, humour, verbosity
SKILLS = (  # config section, label, what it does
    ("productivity", "Productivity", "tasks, reminders, a planner"),
    ("study", "Study helper", "homework help, flashcards, a tutor that guides instead of answering"),
    ("dev", "Developer tools", "repo summaries, git digest, running tests (asks first)"),
    ("create", "Create", "news brief, slides, translation, notes from papers"),
    ("system", "Computer help", "read-only: what is this process, updates, crashes, logs"),
    ("vault", "Second brain", "an encrypted notes vault you can search by voice"),
)


class SetupError(Exception):
    """Something that makes going on pointless (an unsupported Python); the message is shown as is."""


@dataclass
class Facts:
    voice_libs: bool = False
    ffmpeg: bool = False
    claude: bool = False
    browser_lib: bool = False
    chrome: bool = False
    git: bool = False


@dataclass
class Onboarding:
    ui: UI
    env: Env
    store: ConfigStore
    secrets: SecretsStore
    facts: Facts = field(default_factory=Facts)
    summary: dict[str, str] = field(default_factory=dict)
    working_keys: list[str] = field(default_factory=list)

    @property
    def directory(self) -> Path:
        return self.env.directory

    # -- progress that survives a stopped setup ---------------------------------------------------------------------
    @property
    def _state_path(self) -> Path:
        return self.directory / "onboard.json"

    def _state(self) -> dict:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def done_steps(self) -> list[str]:
        return [s for s in self._state().get("done", []) if s in STEP_IDS]

    def _mark(self, step: str) -> None:
        done = self.done_steps()
        if step not in done:
            done.append(step)
        atomic_write_text(self._state_path, json.dumps({"done": done, "summary": self.summary}, indent=1))

    def _edit(self, change) -> None:
        data = self.store.config.model_dump(mode="json")
        change(data)
        self.store.update(data)

    # -- the whole run --------------------------------------------------------------------------------------------
    async def run(self, *, only: str = "", fresh: bool = False) -> bool:
        """Run the steps. True when the person asked to start Minty at the end."""
        ui = self.ui
        ui.banner(TAGLINE)
        if only and only not in STEP_IDS:
            raise SetupError(f"There is no step called {only!r}. The steps are: {', '.join(STEP_IDS)}.")
        order = [only] if only else list(STEP_IDS)
        if not only and not fresh and self.done_steps():
            self.summary.update(self._state().get("summary", {}))
            left = [s for s in STEP_IDS if s not in self.done_steps()]
            pick = ui.choose("You have been here before.", [
                Option("continue", "Continue where I left off", f"next: {dict(STEPS)[left[0]]}" if left else "all done"),
                Option("again", "Start again from the top", "your settings stay until you change them"),
                Option("one", "Pick one step to run again")], default="continue" if left else "again")
            if pick == "continue":
                order = left or ["finish"]
            elif pick == "one":
                order = [ui.choose("Which step?", [Option(s, t) for s, t in STEPS])]
        else:
            ui.note("Welcome", "This takes about five minutes. I will ask one thing at a time.\n"
                               "• You can press Enter to skip anything.\n"
                               "• Nothing is downloaded or installed without asking you first.\n"
                               "• Keys you paste are hidden and kept only on this computer (a file only you can read).\n"
                               "• Stop with Ctrl+C any time and run `minty onboard` to carry on.")
        launch = False
        try:
            for step in order:
                number = STEP_IDS.index(step) + 1
                ui.step(number, len(STEPS), dict(STEPS)[step])
                result = await getattr(self, f"step_{step}")()
                self._mark(step)
                if step == "finish":
                    launch = bool(result)
        except SetupError as e:
            ui.fail(str(e))
            return False
        except (KeyboardInterrupt, EOFError):
            ui.say("")
            ui.warn("Stopped. Run `minty onboard` to carry on from here.")
            return False
        return launch

    # -- 1 ----------------------------------------------------------------------------------------------------------
    async def step_system(self) -> None:
        ui, e, f = self.ui, self.env, self.facts
        if e.python < (3, 10):
            raise SetupError(f"Minty needs Python 3.10 or newer; this is {'.'.join(map(str, e.python))}.")
        ui.ok(f"Python {'.'.join(map(str, e.python))}")
        if e.system == "Linux":
            ui.ok("Linux")
        else:
            ui.warn(f"{e.system or 'This system'}: Minty is built and tested on Linux. Text chat should work; voice and "
                    "system tools may not.")
        f.voice_libs = e.has_module("sounddevice") and e.has_module("onnxruntime")
        f.ffmpeg, f.claude = bool(e.which("ffmpeg")), bool(e.which("claude"))
        f.browser_lib = e.has_module("playwright")
        f.chrome = any(e.which(b) for b in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"))
        f.git = bool(e.which("git"))

        def hint(pkg: str) -> str:
            try:
                return hint_for(detect(), pkg)
            except OSError:
                return f"install {pkg} with your package manager"

        (ui.ok if f.voice_libs else ui.warn)("Voice libraries" + ("" if f.voice_libs else
                                                              " are not installed (step 3 can add them)"))
        (ui.ok if f.ffmpeg else ui.warn)("ffmpeg" + ("" if f.ffmpeg else f" is missing (voice notes from Telegram need it): "
                                                                         f"{hint('ffmpeg')}"))
        (ui.ok if f.claude else ui.say)("Claude Code is installed" if f.claude else
                                        "  · Claude Code is not installed (optional, step 2 explains)")
        (ui.ok if f.chrome else ui.say)("A Chrome or Chromium browser was found" if f.chrome else
                                        "  · No Chrome or Chromium (optional, for the browsing skill)")
        ui.say("")
        ui.say("Config folder: " + str(self.directory))
        self._tune(ui, e)

    def _tune(self, ui, e) -> None:
        """Say what kind of computer this is. Minty looks and works the same everywhere, so nothing is changed here; a slow
        computer is pointed to the lite profile as a choice."""
        from minty_core.hwprobe import probe, recommend
        from minty_core.tune import describe

        try:
            hw = e.hardware() if e.hardware is not None else probe()
        except Exception:  # noqa: BLE001 - a probe that fails is a note, never a stop
            return
        plan = recommend(hw)
        ui.say("")
        for line in describe(hw):
            ui.say(line)
        label = {"t0": "a small or very old computer", "t1": "an ordinary computer", "t2": "a modern computer"}[plan.tier]
        ui.ok(f"This looks like {label}. Minty looks and works the same on every computer.")
        for note in plan.notes:
            ui.say(f"  · {note}")
        self.summary["computer"] = plan.tier

    # -- 2 ----------------------------------------------------------------------------------------------------------
    async def step_brain(self) -> None:
        ui, e, f = self.ui, self.env, self.facts
        ui.say("Minty needs a language model to think. Pick where it comes from.")
        ui.say("")
        brain = ui.choose("Which brain?", [
            Option("free", "Free providers", "recommended: Groq, Gemini and others have free tiers; no card needed"),
            Option("claude", "Claude through my Claude Code login",
                   "uses your Claude subscription; free providers stay as backup" if f.claude else
                   "not installed: curl -fsSL https://claude.ai/install.sh | bash", disabled=not f.claude),
            Option("skip", "Decide later", "add keys any time with: minty set-key groq")], default="free")
        if brain == "skip":
            self.summary["brain"] = "not chosen yet"
            ui.warn("No brain yet. Without one Minty only answers what needs no model.")
            return
        if brain == "claude":
            ui.say(claude_brain("on", self.store, installed=f.claude))
            ui.say("Sign in once by running `claude` in a terminal if you have not already.")
        ui.note("About free tiers", "Free tiers can change their rules. Minty treats Groq as not training on your prompts and Gemini, "
                                    "OpenRouter and Mistral as possibly doing so (check their current terms). Minty never sends private conversations to a provider that is not "
                                    "marked as safe for them, and you can turn on Privacy mode in step 5.")
        have = [p[0] for p in PROVIDERS if self.secrets.get(p[0])]
        wanted = ui.multi("Which free providers do you have a key for, or want to add?",
                          [Option(k, label, why + (" (key already saved)" if k in have else "")) for k, label, why, _ in PROVIDERS],
                          defaults=("groq",) if not have else ())
        saved = []
        for key, label, _why, url in PROVIDERS:
            if key not in wanted:
                continue
            if key in have and not ui.confirm(f"{label} already has a key. Replace it?", default=False):
                continue
            ui.say(f"Get a free {label} key at {url}")
            value = ui.ask(f"Paste the {label} key (hidden, Enter to skip)", secret=True)
            if value:
                self.secrets.set(key, value)
                saved.append(key)
        if (saved or have) and e.probe is not None:
            with ui.working("Checking your keys"):
                try:
                    found = await asyncio.wait_for(e.probe(), 60)
                except Exception as err:  # noqa: BLE001 - a failed check is reported, never fatal
                    found = {}
                    ui.warn(f"I could not check the keys just now ({type(err).__name__}).")
            self.working_keys = sorted(n for n, s in found.items() if s == "ok")
            for name in sorted(found):
                (ui.ok if found[name] == "ok" else ui.fail)(f"{name}: {'works' if found[name] == 'ok' else found[name]}")
        names = ", ".join(self.working_keys or saved or have)
        self.summary["brain"] = (("Claude + " if brain == "claude" else "")
                                 + (f"free providers ({names})" if names else "no free keys yet"))
        if brain == "free" and not (saved or have):
            ui.warn("No keys saved. Add one later with: minty set-key groq")

    # -- 3 ----------------------------------------------------------------------------------------------------------
    async def step_voice(self) -> None:
        ui, e, f = self.ui, self.env, self.facts
        f.voice_libs = e.has_module("sounddevice") and e.has_module("onnxruntime")
        how = ui.choose("How will you mostly talk to Minty?", [
            Option("voice", "Out loud", "say the wake word, it answers by voice"),
            Option("text", "By typing", "the terminal, the web panel and Telegram; no microphone needed")], default="voice")
        if how == "text":
            self.summary["voice"] = "text only"
            ui.say("Fine. You can turn voice on later with: minty onboard --step voice")
            return
        if not f.voice_libs and ui.confirm("The voice libraries are not installed. Install them now (a few hundred MB)?"):
            with ui.working("Installing the voice libraries"):
                ok, tail = e.install("minty-core[voice]")
            (ui.ok if ok else ui.fail)("Voice libraries installed." if ok else f"That did not work:\n{tail}")
            f.voice_libs = ok and e.has_module("sounddevice")
        engine = ui.choose("Which voice should Minty speak with?", [
            Option("piper", "Piper", f"light and fast, works on any computer; download {VOICE_SIZE['piper']}"),
            Option("kokoro", "Kokoro", f"more natural, needs a decent CPU; download {VOICE_SIZE['kokoro']}"),
            Option("none", "Set up later")], default="piper")
        voice_model = ""
        if engine != "none":
            if ui.confirm(f"Download the {engine} voice now ({VOICE_SIZE[engine]})?"):
                results = [e.download(url, e.data / "voices" / name) for url, name in VOICE_FILES[engine]]
                good = all(ok for ok, _ in results)
                for ok, message in results:
                    (ui.ok if ok else ui.fail)(message)
                if good and engine == "piper":
                    voice_model = str(e.data / "voices" / "en_US-lessac-medium.onnx")
                if not good:
                    ui.warn("The download did not finish; run `minty onboard --step voice` to try again.")
                    engine = "none"
            else:
                engine = "none"
        live = bool(self.secrets.get("gemini"))
        mode = "classic"
        if live:
            mode = ui.choose("Which kind of voice conversation?", [
                Option("live", "Gemini Live", "speech to speech, very natural, uses your Gemini key"),
                Option("classic", "Classic", "speech recognition, then your brain, then the voice you chose")], default="live")
        language = ui.ask("Language you will speak (a two-letter code like en, hi, es)", default="en").lower()
        if not re.fullmatch(r"[a-z]{2,3}", language):
            ui.warn("That is not a language code, using en.")
            language = "en"
        listening = ui.choose("What do you listen on?", [
            Option("headphones", "Headphones", "Minty can listen while it talks"),
            Option("speakers", "Speakers", "Minty mutes the microphone while it talks"),
            Option("unknown", "Not sure")], default="unknown")

        def change(d: dict) -> None:
            d["audio"]["voice_mode"] = mode
            d["audio"]["listening_setup"] = listening
            d["audio"]["live_echo_guard"] = listening != "headphones"
            d["stt"]["language"] = language
            d["stt"]["allowed_languages"] = [language]
            d["stt"]["provider"] = "groq" if self.secrets.get("groq") else "local"
            if engine != "none":
                d["tts"]["engine"] = engine
            if voice_model:
                d["tts"]["voice_model"] = voice_model

        self._edit(change)
        if f.voice_libs and ui.confirm("Test the microphone now? Say a short sentence when asked."):
            ok, message = e.mic_test()
            (ui.ok if ok else ui.warn)(message)
        self.summary["voice"] = f"{mode}, {engine if engine != 'none' else 'voice not set up yet'}, {language}, {listening}"
        ui.say("Wake word: say \"Minty\".")

    # -- 4 ----------------------------------------------------------------------------------------------------------
    async def step_you(self) -> None:
        ui = self.ui
        name = ui.ask("What should Minty call you? (Enter to skip)")
        if name:
            memory = MemoryStore(self.directory / "memory.md")
            for i in reversed([i for i, n in enumerate(memory.list()) if n.startswith("The user's name is")]):
                memory.delete(i)
            memory.add(f"The user's name is {name}.")
        persona = ui.choose("Pick a personality.", [Option(k, k.capitalize(), PERSONA_HINTS.get(k, ""))
                                                       for k in DEFAULT_PERSONALITIES], default="minty")
        tone = ui.choose("How should it sound?", [
            Option("serious", "Short and serious"), Option("balanced", "Balanced"),
            Option("chatty", "Chatty and funny")], default="balanced")
        formality, humor, verbosity = TONES[tone]

        def change(d: dict) -> None:
            d["personality"].update(active=persona, formality=formality, humor=humor, verbosity=verbosity)

        self._edit(change)
        self.summary["you"] = f"{name or 'name not given'}, {persona}, {tone}"

    # -- 5 ----------------------------------------------------------------------------------------------------------
    async def step_safety(self) -> None:
        ui = self.ui
        ui.note("How careful should Minty be?", "Whatever you pick, Minty always asks before spending money, using a password "
                                               "or deleting something for good. This sets how much else it asks about.")
        mode = ui.choose("Safety level", [
            Option("careful", "Careful", "asks before any change"),
            Option("commits", "Balanced", "asks before anything other people see or that is hard to undo (recommended)"),
            Option("trusted", "Trusting", "asks only for money, credentials and deleting")], default="commits")
        private = ui.confirm("Turn on Privacy mode? (only providers that do not train on your data, no web tools)", default=False)

        def change(d: dict) -> None:
            d["safety"]["mode"] = mode
            d["safety"]["privacy_mode"] = private

        self._edit(change)
        self.summary["safety"] = f"{mode}{', privacy mode' if private else ''}"

    # -- 6 ----------------------------------------------------------------------------------------------------------
    async def step_phone(self) -> None:
        ui, e = self.ui, self.env
        if not ui.confirm("Talk to Minty from your phone with Telegram? (optional, free)", default=False):
            self.summary["phone"] = "not set up"
            return
        ui.note("Make your own bot (2 minutes)", "1. Open Telegram and search for @BotFather.\n2. Send /newbot and follow the "
                                                 "questions.\n3. BotFather replies with a token. Copy it.\nOnly chats you pair "
                                                 "with a one-time code are ever answered.")
        token = ui.ask("Paste the bot token (hidden, Enter to skip)", secret=True)
        if not token:
            self.summary["phone"] = "not set up"
            return
        username = ""
        if e.telegram_me is not None:
            with ui.working("Checking the token with Telegram"):
                try:
                    username = await asyncio.wait_for(e.telegram_me(token), 20)
                except Exception as err:  # noqa: BLE001
                    ui.fail(f"Telegram did not accept that token ({type(err).__name__}). Nothing was saved. "
                            "Check it with @BotFather and run `minty onboard --step phone`.")
                    self.summary["phone"] = "token not accepted"
                    return
        self.secrets.set("telegram", token)
        self._edit(lambda d: d["telegram"].update(enabled=True))
        ui.ok(f"Bot {'@' + username if username else ''} saved and Telegram switched on.")
        ui.say("When Minty is running, pair your chat with:  minty telegram-pair")
        self.summary["phone"] = f"Telegram {'@' + username if username else 'bot'} (pair it when Minty runs)"

    # -- 7 ----------------------------------------------------------------------------------------------------------
    async def step_extras(self) -> None:
        ui, e, f = self.ui, self.env, self.facts
        skills = ui.multi("Which skills should be on? (you can change this later in the panel)",
                          [Option(k, label, what) for k, label, what in SKILLS], defaults=("productivity",))

        def change(d: dict) -> None:
            for key, _l, _w in SKILLS:
                d[key]["enabled"] = key in skills

        self._edit(change)
        wants = ui.multi("Extra packages? Each is installed only if ticked, after you confirm.", [
            Option("browser", "Browsing", "Minty can open sites and fill forms (Playwright + Chromium, about 200 MB)"),
            Option("backup", "Encrypted backups", "a small package (cryptography)"),
            Option("documents", "Read PDFs", "a small package (pypdf)")])
        done = []
        for want in wants:
            if not ui.confirm(f"Install {want} support now?"):
                continue
            with ui.working(f"Installing {want}"):
                ok, tail = e.install(f"minty-core[{want}]")
                if ok and want == "browser" and not f.chrome:
                    ok, tail = e.run([sys.executable, "-m", "playwright", "install", "chromium"])
            (ui.ok if ok else ui.fail)(f"{want} ready" if ok else f"{want} did not install:\n{tail}")
            if ok:
                done.append(want)
        self.summary["skills"] = ", ".join(skills) or "none"
        if done:
            self.summary["extras"] = ", ".join(done)

    def _has_brain(self) -> bool:
        """A model Minty can ask: a saved provider key, or Claude switched on."""
        return (any(self.secrets.get(k) for k, *_ in PROVIDERS)
                or any(p.name in CLAUDE_BRAIN for p in self.store.config.providers))

    # -- 8 ----------------------------------------------------------------------------------------------------------
    async def step_finish(self) -> bool:
        ui, e = self.ui, self.env
        ui.table("Your setup", [(k.capitalize(), v) for k, v in self.summary.items()] or [("Setup", "nothing chosen yet")])
        if e.hello is not None and self._has_brain():
            if ui.confirm("Ask Minty to say hello, as a real test?"):
                with ui.working("Asking"):
                    try:
                        reply = await asyncio.wait_for(e.hello(), 90)
                    except Exception as err:  # noqa: BLE001
                        reply = ""
                        ui.fail(f"No answer ({type(err).__name__}). Check the keys with: minty set-key groq")
                if reply:
                    ui.ok(f"Minty: {reply}")
        ui.note("Next", "• Start Minty any time with:  minty\n• The web panel is at http://127.0.0.1:7778 (set its password "
                        "with: minty set-password)\n• Change anything later in the panel, or re-run one step: "
                        "minty onboard --step voice\n• Help: docs/getting-started.md")
        if e.root is not None and e.which("systemctl") and (e.root / ".venv" / "bin" / "python").exists():
            if ui.confirm("Keep Minty running in the background and start it when you log in?", default=False):
                ok, tail = e.run(["bash", str(e.root / "scripts" / "install_service.sh")])
                (ui.ok if ok else ui.fail)("Minty will start at login." if ok else f"That did not work:\n{tail}")
        return ui.confirm("Start Minty now?", default=True)
