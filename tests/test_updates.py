"""Update policy: staged, never automatic; key ages (W1B-07)."""
import json
import subprocess
import sys
from pathlib import Path

from minty_core import updates
from minty_core.keystore import SecretsStore

DAY = 86400.0


def test_key_ages_come_from_when_each_key_was_set(tmp_path):
    s = SecretsStore(tmp_path)
    s.set("groq", "gsk-not-real-1")
    s._stamp(["groq"], now=1_000_000.0)
    s.set("gemini", "AIza-not-real")
    s._stamp(["gemini"], now=1_000_000.0 + 100 * DAY)
    ages = s.ages_days(now=1_000_000.0 + 120 * DAY)
    assert ages == {"groq": 120, "gemini": 20}
    assert updates.old_keys(ages) == ["groq"]
    text = updates.key_reminder(ages)
    assert "groq (120 days)" in text and "gemini" not in text and "gsk-not" not in text
    assert updates.key_reminder({"a": 3}) == ""
    assert "gsk-not-real-1" not in (tmp_path / "secrets.dates.json").read_text()  # dates only, never values


class Runner:
    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def __call__(self, argv, cwd):
        self.calls.append(argv)
        for key, value in self.replies.items():
            if argv[: len(key)] == list(key):
                return value
        return 0, ""


def test_prepare_records_the_commit_the_settings_and_the_package_versions(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "config.toml").write_text("a = 1\n")
    r = Runner({("git", "rev-parse"): (0, "abc1234deadbeef\n"), ("git", "status"): (0, ""),
                ("pip", "freeze"): (0, "httpx==0.27.0\nnumpy==1.26.4\n")})
    state = updates.prepare(tmp_path, cfg, r, now=lambda: 5.0)
    saved = json.loads((cfg / "update-state" / "last-good.json").read_text())
    assert state["commit"] == saved["commit"] == "abc1234deadbeef" and saved["clean"] is True
    assert (cfg / "update-state" / "config.toml.before-update").read_text() == "a = 1\n"


def test_check_passes_or_fails_with_the_exact_rollback(tmp_path):
    cfg = tmp_path / "cfg"
    (cfg / "update-state").mkdir(parents=True)
    (cfg / "update-state" / "last-good.json").write_text(json.dumps(
        {"commit": "abc1234deadbeef", "freeze": ["httpx==0.27.0", "numpy==1.26.4"]}))
    good = Runner({("python", "-m", "pytest"): (0, "2040 passed in 190s"), ("python", "scripts/doctor.py"): (0, "ok"),
                   ("pip", "freeze"): (0, "httpx==0.28.0\nnumpy==1.26.4\n")})
    result = updates.check(tmp_path, cfg, good)
    assert result.ok and result.steps[-1] == "packages: httpx==0.27.0 -> httpx==0.28.0"
    bad = Runner({("python", "-m", "pytest"): (1, "1 failed, 2039 passed")})
    failed = updates.check(tmp_path, cfg, bad)
    assert not failed.ok and failed.rollback == "git checkout abc1234deadbeef" and "FAILED" in failed.steps[0]
    assert len(bad.calls) == 1  # it stopped at the first failure
    assert not updates.check(tmp_path, tmp_path / "empty", good).ok  # not prepared


def test_the_script_explains_itself():
    root = Path(__file__).resolve().parents[1]
    out = subprocess.run([sys.executable, str(root / "scripts" / "staged_update.py"), "help"], capture_output=True,
                         text=True, env={"PYTHONPATH": str(root / "src"), "PATH": "/usr/bin:/bin"})
    assert out.returncode == 2 and "prepare" in out.stdout and "never automatic" in out.stdout
