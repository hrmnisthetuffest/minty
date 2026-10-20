"""The package, commands, wake word and prompt all say Minty."""
import subprocess
import sys
import tomllib
from pathlib import Path

from minty_core.config import Config
from minty_core.personality import BASE_RULES, DEFAULT_PERSONALITIES, compile_prompt

ROOT = Path(__file__).resolve().parents[1]


def test_package_and_commands_are_minty():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert meta["project"]["name"] == "minty-core"
    assert meta["project"]["scripts"] == {"minty": "minty_core.app:main", "minty-core": "minty_core.app:main",
                                          "minty-panel": "minty_panel.main:main"}


def test_wake_word_is_minty_by_default():
    cfg = Config()
    assert cfg.audio.wake_phrases[0] == "minty"
    assert cfg.stt.hotwords == "Minty"


def test_the_prompt_names_the_assistant_and_how_to_say_it():
    assert set(DEFAULT_PERSONALITIES) == {"minty", "jarvis", "casual"}
    prompt = compile_prompt(DEFAULT_PERSONALITIES["minty"], Config().personality, "")
    assert "You are Minty" in prompt and '"Minty"' in prompt and BASE_RULES in prompt


def test_it_starts_as_a_module_and_through_the_launcher_script():
    env = {"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"}
    out = subprocess.run([sys.executable, "-m", "minty_core", "--help"], capture_output=True, text=True, cwd=ROOT,
                         env=env)
    assert out.returncode == 0 and "minty-core" in out.stdout
    via_script = subprocess.run([str(ROOT / "bin" / "minty"), "--help"], capture_output=True, text=True,
                                env={**env, "MINTY_PYTHON": sys.executable})
    assert via_script.returncode == 0 and via_script.stdout == out.stdout
