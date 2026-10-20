"""First-run setup: keys, a key check, the language, a microphone test and how you listen, in a few plain steps.

Everything is injected (input, output, key store, config store, the key probe and the microphone test), so it runs
the same in a terminal and in a test. Keys are asked for without echo and are only ever passed to the key store.
Nothing is downloaded. Skipping a step is always allowed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Protocol

FREE_PROVIDERS = ("groq", "gemini", "openrouter", "nvidia", "cerebras", "mistral")
LISTENING = {"headphones": "headphones", "speakers": "speakers", "unknown": "unknown", "": "unknown"}


class IO(Protocol):
    def say(self, text: str) -> None: ...
    def ask(self, prompt: str, default: str = "", secret: bool = False) -> str: ...


def needs_setup(config_dir: Path) -> bool:
    """True on a directory that has never been set up: no config file and no keys."""
    d = Path(config_dir)
    return not (d / "config.toml").exists() and not (d / "secrets.toml").exists()


@dataclass
class WizardResult:
    keys_saved: list[str] = field(default_factory=list)
    working: list[str] = field(default_factory=list)
    language: str = "en"
    mic_ok: bool | None = None
    listening: str = "unknown"
    skipped: list[str] = field(default_factory=list)


def mic_level_test(seconds: float = 3.0) -> tuple[bool, str]:
    """Record a few seconds and say whether anything usable arrived. Needs the voice extra; says so if missing."""
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError:
        return False, "The voice libraries are not installed, so I can't test the microphone."
    try:
        audio = sd.rec(int(seconds * 16000), samplerate=16000, channels=1, dtype="int16")
        sd.wait()
    except Exception as e:  # noqa: BLE001 — a device problem is the answer, not a crash
        return False, f"The microphone could not be opened ({type(e).__name__})."
    samples = audio.astype("float32")
    level, peak = int(np.sqrt(np.mean(samples ** 2))), int(np.abs(samples).max())
    if level < 50:
        return False, "I heard almost nothing. Check that the microphone is not muted and is the right device."
    if peak >= 32000:
        return True, "I heard you, but it is very loud and clipping; lower the microphone gain a little."
    return True, f"I heard you clearly (level {level})."


class Wizard:
    def __init__(self, io: IO, secrets, store, *, probe: Callable[[], Awaitable[dict[str, str]]] | None = None,
                 mic_test: Callable[[], tuple[bool, str]] | None = None):
        self.io, self.secrets, self.store = io, secrets, store
        self._probe, self._mic = probe, mic_test

    async def run(self) -> WizardResult:
        r = WizardResult()
        say = self.io.say
        say("Setting up Minty You can skip anything by pressing Enter; nothing is downloaded.")
        # 1. keys
        say("Step 1 of 5: free model keys. Paste a key to save it (it is not shown), or press Enter to skip.")
        for name in FREE_PROVIDERS:
            value = self.io.ask(f"Key for {name}", secret=True).strip()
            if value:
                self.secrets.set(name, value)
                r.keys_saved.append(name)
        if not r.keys_saved:
            r.skipped.append("keys")
            say("No keys saved. You can add one later with: minty-core set-key groq")
        # 2. check them
        say("Step 2 of 5: checking the keys.")
        if r.keys_saved and self._probe is not None:
            try:
                found = await self._probe()
            except Exception as e:  # noqa: BLE001
                found = {}
                say(f"I couldn't check them just now ({type(e).__name__}).")
            r.working = sorted(n for n, state in found.items() if state == "ok")
            bad = sorted(n for n, state in found.items() if state != "ok")
            if r.working:
                say("Working: " + ", ".join(r.working) + ".")
            if bad:
                say("Not working: " + ", ".join(f"{n} ({found[n]})" for n in bad) + ".")
        else:
            r.skipped.append("check")
        # 3. language
        say("Step 3 of 5: language. I speak English unless you tell me to use another one.")
        wanted = self.io.ask("Language code", default="en").strip().lower() or "en"
        r.language = wanted if len(wanted) <= 5 and wanted.replace("-", "").isalpha() else "en"
        # 4. microphone
        say("Step 4 of 5: microphone test. Say a short sentence when asked.")
        if self._mic is not None and self.io.ask("Run the microphone test now? (y/n)", default="y").lower().startswith("y"):
            ok, message = self._mic()
            r.mic_ok = ok
            say(message)
        else:
            r.skipped.append("microphone")
        # 5. how you listen (echo handling is set up later from this)
        say("Step 5 of 5: do you usually listen on headphones or on speakers?")
        r.listening = LISTENING.get(self.io.ask("headphones / speakers", default="unknown").strip().lower(), "unknown")
        say("Telegram (your phone) is set up later from the panel; it needs a bot token from BotFather.")
        self._save(r)
        say("Done. Start me with: minty   (or bin/minty). Say \"Minty\" to wake me.")
        return r

    def _save(self, r: WizardResult) -> None:
        data = self.store.config.model_dump(mode="json")
        data["stt"]["language"] = r.language
        data["stt"]["allowed_languages"] = [r.language]
        data["audio"]["listening_setup"] = r.listening
        if r.keys_saved and not r.working and self._probe is None:
            pass  # no probe available: leave providers as they are
        self.store.update(data)
