"""The guided setup: every step on a clean directory with scripted answers, plus the terminal UI itself."""
import io
import json
from contextlib import contextmanager

import pytest
from rich.console import Console

from minty_core.app import parse_args
from minty_core.config import ConfigStore
from minty_core.keystore import SecretsStore
from minty_core.memory import MemoryStore
from minty_core.onboard.env import VOICE_FILES, Env
from minty_core.onboard.steps import STEP_IDS, Onboarding, SetupError
from minty_core.onboard.ui import Option, RichUI

KEY = "gsk_this_is_a_fake_test_key_0123456789"


class ScriptedUI:
    """Answers by a word in the question; anything not scripted takes the default."""

    def __init__(self, script=None, stop_at=None):
        self.script, self.said, self.asked, self.stop_at = dict(script or {}), [], [], stop_at

    def _answer(self, prompt, default):
        self.asked.append(prompt)
        if self.stop_at and self.stop_at in prompt:
            raise KeyboardInterrupt
        for word, value in self.script.items():
            if word in prompt:
                return value
        return default

    def banner(self, tagline): self.said.append(tagline)
    def step(self, n, total, title, blurb=""): self.said.append(f"STEP {n}/{total} {title}")
    def say(self, text): self.said.append(text)
    def ok(self, text): self.said.append("ok " + text)
    def warn(self, text): self.said.append("warn " + text)
    def fail(self, text): self.said.append("fail " + text)
    def note(self, title, body): self.said.append(f"{title}: {body}")
    def table(self, title, rows): self.said.append(title + " " + "; ".join(f"{k}={v}" for k, v in rows))
    def ask(self, prompt, default="", secret=False): return self._answer(prompt, default)
    def confirm(self, prompt, default=True): return self._answer(prompt, default)

    def choose(self, prompt, options, default=""):
        return self._answer(prompt, default or next(o.value for o in options if not o.disabled))

    def multi(self, prompt, options, defaults=()): return self._answer(prompt, list(defaults))

    @contextmanager
    def working(self, text):
        yield


def make(tmp_path, script=None, stop_at=None, **env):
    store = ConfigStore(tmp_path)
    store.load()
    calls = {"install": [], "run": [], "download": []}

    async def probe(): return {"groq": "ok", "gemini": "bad key"}
    async def me(token): return "minty_test_bot" if token == "123:good" else (_ for _ in ()).throw(ValueError("no"))
    async def hello(): return "Hello there."

    defaults = dict(
        directory=tmp_path, which=lambda n: f"/usr/bin/{n}" if n in {"apt", "ffmpeg", "git"} else None,
        has_module=lambda n: n in {"sounddevice", "onnxruntime"}, python=(3, 12, 0), system="Linux", root=None,
        data=tmp_path / "data", mic_test=lambda: (True, "I heard you clearly."),
        install=lambda spec: (calls["install"].append(spec), (True, ""))[1],
        run=lambda cmd: (calls["run"].append(cmd), (True, ""))[1],
        download=lambda url, dest: (calls["download"].append((url, dest.name)), (True, f"{dest.name} saved."))[1],
        probe=probe, telegram_me=me, hello=hello)
    defaults.update(env)
    ui = ScriptedUI(script, stop_at)
    return Onboarding(ui, Env(**defaults), store, SecretsStore(tmp_path)), ui, calls


FULL = {"Which brain": "free", "Which free providers": ["groq"], "Paste the Groq key": KEY,
        "How will you mostly talk": "text", "What should Minty call you": "Alex", "Pick a personality": "jarvis",
        "How should it sound": "chatty", "Safety level": "careful", "Privacy mode": False,
        "from your phone": False, "Which skills": ["study", "dev"], "Extra packages": [], "Start Minty now": True}


async def test_a_whole_setup_on_a_clean_directory_saves_what_was_chosen(tmp_path):
    ob, ui, _ = make(tmp_path, FULL)
    assert await ob.run() is True
    cfg = ob.store.config
    assert (cfg.personality.active, cfg.personality.humor, cfg.safety.mode) == ("jarvis", 7, "careful")
    assert cfg.study.enabled and cfg.dev.enabled and not cfg.productivity.enabled and not cfg.telegram.enabled
    assert ob.secrets.get("groq") == KEY
    assert "The user's name is Alex." in MemoryStore(tmp_path / "memory.md").list()
    state = json.loads((tmp_path / "onboard.json").read_text())
    assert state["done"] == list(STEP_IDS)
    said = "\n".join(ui.said)
    assert "groq: works" in said and "gemini: bad key" in said and "Minty: Hello there." in said
    assert KEY not in said and KEY not in (tmp_path / "onboard.json").read_text()


async def test_a_stopped_setup_carries_on_where_it_left_off(tmp_path):
    ob, _, _ = make(tmp_path, FULL, stop_at="How will you mostly talk")
    assert await ob.run() is False
    assert ob.done_steps() == ["system", "brain"]
    ob2, ui2, _ = make(tmp_path, {**FULL, "You have been here before": "continue"})
    assert await ob2.run() is True
    assert [s for s in ui2.said if s.startswith("STEP")][0].startswith("STEP 3/8")
    assert ob2.done_steps() == list(STEP_IDS)


async def test_one_step_can_be_run_alone_and_an_unknown_step_is_refused(tmp_path):
    ob, ui, _ = make(tmp_path, FULL)
    await ob.run(only="safety")
    assert ob.store.config.safety.mode == "careful" and ob.done_steps() == ["safety"]
    assert sum(s.startswith("STEP") for s in ui.said) == 1
    with pytest.raises(SetupError):
        await ob.run(only="nope")


async def test_an_old_python_stops_the_setup_with_a_plain_sentence(tmp_path):
    ob, ui, _ = make(tmp_path, FULL, python=(3, 9, 18))
    assert await ob.run() is False
    assert any(s.startswith("fail Minty needs Python 3.10") for s in ui.said) and ob.done_steps() == []


async def test_claude_cannot_be_chosen_when_it_is_not_installed(tmp_path):
    seen = []
    ob, ui, _ = make(tmp_path, {"Which brain": "claude"})
    original = ui.choose
    ui.choose = lambda prompt, options, default="": (seen.append(options), original(prompt, options, default))[1]
    await ob.step_system()
    await ob.step_brain()
    claude = [o for o in seen[0] if o.value == "claude"][0]
    assert claude.disabled and "install.sh" in claude.hint


async def test_claude_brain_is_switched_on_when_chosen_and_installed(tmp_path):
    ob, _, _ = make(tmp_path, {**FULL, "Which brain": "claude"},
                    which=lambda n: "/usr/bin/claude" if n == "claude" else None)
    await ob.step_system()
    await ob.step_brain()
    names = [p.name for p in ob.store.config.providers]
    assert "claude" in names and "groq" in names and ob.store.config.preferred_provider == "claude"


async def test_voice_asks_before_installing_and_downloading_and_sets_the_voice(tmp_path):
    ob, ui, calls = make(tmp_path, {"How will you mostly talk": "voice", "Install them now": True,
                                    "Which voice": "piper", "Download the piper": True, "Language": "hi",
                                    "What do you listen on": "speakers", "Test the microphone": True},
                         has_module=lambda n: False)
    await ob.step_voice()
    assert calls["install"] == ["minty-core[voice]"]
    assert [u for u, _ in calls["download"]] == [u for u, _ in VOICE_FILES["piper"]]
    cfg = ob.store.config
    assert cfg.tts.engine == "piper" and cfg.tts.voice_model.endswith("en_US-lessac-medium.onnx")
    assert (cfg.stt.language, cfg.stt.allowed_languages, cfg.audio.listening_setup) == ("hi", ["hi"], "speakers")
    assert cfg.audio.live_echo_guard is True


async def test_nothing_is_downloaded_when_you_say_no(tmp_path):
    ob, _, calls = make(tmp_path, {"How will you mostly talk": "voice", "Download the": False, "Install them": False})
    before = ob.store.config.tts.engine
    await ob.step_voice()
    assert calls["download"] == [] and calls["install"] == [] and ob.store.config.tts.engine == before


async def test_a_failed_download_leaves_the_voice_settings_alone(tmp_path):
    ob, ui, _ = make(tmp_path, {"How will you mostly talk": "voice", "Which voice": "piper"},
                     download=lambda url, dest: (False, f"{dest.name}: ConnectError"))
    await ob.step_voice()
    assert ob.store.config.tts.voice_model == "" and any("did not finish" in s for s in ui.said)


async def test_gemini_live_is_offered_only_with_a_gemini_key(tmp_path):
    ob, _, _ = make(tmp_path, {"How will you mostly talk": "voice", "Which voice": "none", "Which kind of voice": "live"})
    await ob.step_voice()
    assert ob.store.config.audio.voice_mode == "classic"
    ob.secrets.set("gemini", "AIza-fake-key-for-the-test")
    await ob.step_voice()
    assert ob.store.config.audio.voice_mode == "live"


async def test_a_good_telegram_token_is_saved_and_switches_telegram_on(tmp_path):
    ob, ui, _ = make(tmp_path, {"from your phone": True, "Paste the bot token": "123:good"})
    await ob.step_phone()
    assert ob.secrets.get("telegram") == "123:good" and ob.store.config.telegram.enabled
    assert "123:good" not in "\n".join(ui.said) and any("minty_test_bot" in s for s in ui.said)


async def test_a_bad_telegram_token_saves_nothing(tmp_path):
    ob, _, _ = make(tmp_path, {"from your phone": True, "Paste the bot token": "999:bad"})
    await ob.step_phone()
    assert ob.secrets.get("telegram") is None and not ob.store.config.telegram.enabled


async def test_extras_install_only_what_was_ticked_and_confirmed(tmp_path):
    ob, _, calls = make(tmp_path, {"Which skills": [], "Extra packages": ["browser", "backup"], "Install backup": False})
    await ob.step_system()
    await ob.step_extras()
    assert calls["install"] == ["minty-core[browser]"] and calls["run"][0][-3:] == ["playwright", "install", "chromium"]


async def test_re_running_the_name_replaces_it_instead_of_adding_another(tmp_path):
    ob, _, _ = make(tmp_path, {"What should Minty call you": "Alex"})
    await ob.step_you()
    ob.ui.script["What should Minty call you"] = "Sam"
    await ob.step_you()
    assert [n for n in MemoryStore(tmp_path / "memory.md").list() if "name is" in n] == ["The user's name is Sam."]


def test_the_command_line_knows_onboard_and_setup():
    args = parse_args(["onboard", "--step", "voice", "--again"])
    assert (args.command, args.step, args.again) == ("onboard", "voice", True)
    assert parse_args(["setup"]).command == "setup"


# -- the terminal UI --------------------------------------------------------------------------------------------------

def _ui(keys=(), interactive=True):
    out = io.StringIO()
    it = iter(keys)
    return RichUI(Console(file=out, force_terminal=True, width=100), keys=lambda: next(it), interactive=interactive), out


OPTS = [Option("a", "Alpha", "first"), Option("b", "Beta"), Option("c", "Gamma", "off", disabled=True)]


def test_arrow_keys_move_and_enter_picks():
    ui, out = _ui(["down", "enter"])
    assert ui.choose("Pick", OPTS, default="a") == "b"
    assert "Alpha" in out.getvalue() and "Beta" in out.getvalue()


def test_a_disabled_option_cannot_be_picked():
    ui, _ = _ui(["down", "down", "enter", "up", "enter"])
    assert ui.choose("Pick", OPTS) == "b"


def test_space_ticks_and_enter_confirms_a_multiple_choice():
    ui, _ = _ui(["space", "down", "space", "down", "space", "enter"])
    assert ui.multi("Pick some", OPTS, defaults=()) == ["a", "b"]


def test_without_a_terminal_it_asks_by_number(monkeypatch):
    ui, _ = _ui(interactive=False)
    monkeypatch.setattr("builtins.input", lambda: "2")
    assert ui.choose("Pick", OPTS, default="a") == "b"
    answers = iter(["", "1,2,3"])
    monkeypatch.setattr("builtins.input", lambda: next(answers))
    assert ui.multi("Pick some", OPTS, defaults=("a",)) == ["a"]
    assert ui.multi("Pick some", OPTS, defaults=()) == ["a", "b"]


def test_secrets_are_read_without_echo_and_the_banner_prints(monkeypatch):
    ui, out = _ui()
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "  hidden-value ")
    assert ui.ask("Key", secret=True) == "hidden-value"
    ui.banner("tagline here")
    ui.step(2, 8, "Choose a brain", "blurb")
    ui.table("Your setup", [("Brain", "free")])
    assert "tagline here" in out.getvalue() and "2/8" in out.getvalue() and "hidden-value" not in out.getvalue()


# -- the computer: tier, never a change to how Minty looks or works ------------------------------------------------------

def _hw(tmp_path, **kw):
    from minty_core.hwprobe import probe
    from tests.test_hwprobe import machine
    return lambda: probe(machine(tmp_path / "hw", **kw), run=lambda cmd: "")


async def test_an_old_computer_is_described_and_nothing_about_minty_is_changed(tmp_path):
    from tests.test_hwprobe import OLD_FLAGS
    ob, ui, _ = make(tmp_path, {}, hardware=_hw(tmp_path, ram_kb=3_800_000, threads=2, flags=OLD_FLAGS))
    before = ob.store.config.model_dump(mode="json")
    await ob.step_system()
    assert ob.store.config.model_dump(mode="json") == before  # the same look and the same features everywhere
    said = "\n".join(ui.said)
    assert "small or very old computer" in said and "same on every computer" in said and "minty tune --lite" in said
    assert ob.summary["computer"] == "t0" and not any(q.startswith("Use the") for q in ui.asked)


async def test_a_modern_computer_is_described_the_same_way(tmp_path):
    ob, ui, _ = make(tmp_path, {}, hardware=_hw(tmp_path, ram_kb=32_000_000, threads=16))
    await ob.step_system()
    assert ob.summary["computer"] == "t2" and "modern computer" in "\n".join(ui.said)
