import os
import sys
from pathlib import Path

import pytest

from minty_core.plugins.builtin.agents import verify


def touch(path: Path, text: str, mtime: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, ns=(mtime, mtime))


def test_changes_are_found_by_what_is_on_disk(tmp_path):
    touch(tmp_path / "a.py", "one", 1_000_000_000)
    touch(tmp_path / "sub" / "b.py", "two", 1_000_000_000)
    touch(tmp_path / "gone.txt", "bye", 1_000_000_000)
    before = verify.fingerprint(tmp_path)
    touch(tmp_path / "a.py", "ONE", 2_000_000_000)  # same size, newer
    touch(tmp_path / "new.md", "hello", 2_000_000_000)
    (tmp_path / "gone.txt").unlink()
    assert verify.changed(before, verify.fingerprint(tmp_path)) == ["a.py", "gone.txt", "new.md"]


def test_nothing_changed_is_an_empty_list(tmp_path):
    touch(tmp_path / "a.py", "one", 1_000_000_000)
    before = verify.fingerprint(tmp_path)
    assert verify.changed(before, verify.fingerprint(tmp_path)) == []


@pytest.mark.parametrize("junk", [".git/index", "__pycache__/x.pyc", "node_modules/p/i.js", ".venv/lib/x.py",
                                  ".pytest_cache/v/x"])
def test_tool_noise_does_not_count_as_a_change(tmp_path, junk):
    touch(tmp_path / "a.py", "one", 1_000_000_000)
    before = verify.fingerprint(tmp_path)
    touch(tmp_path / junk, "noise", 2_000_000_000)
    assert verify.changed(before, verify.fingerprint(tmp_path)) == []


def test_a_huge_folder_is_capped_and_says_so(tmp_path):
    for i in range(12):
        touch(tmp_path / f"f{i}.txt", "x", 1_000_000_000)
    assert verify.fingerprint(tmp_path, limit=5).truncated is True
    assert verify.fingerprint(tmp_path, limit=50).truncated is False


def test_a_python_project_is_checked_with_pytest(tmp_path):
    touch(tmp_path / "pyproject.toml", "[project]\nname='x'\n", 1)
    touch(tmp_path / "tests" / "test_a.py", "def test_a(): pass\n", 1)
    argv = verify.detect_check(tmp_path)
    assert argv[1:] == ["-m", "pytest", "-q", "-x"] and argv[0].endswith("python3")


def test_the_projects_own_virtualenv_is_preferred(tmp_path):
    touch(tmp_path / "tests" / "test_a.py", "def test_a(): pass\n", 1)
    py = tmp_path / ".venv" / "bin" / "python"
    touch(py, "", 1)
    py.chmod(0o755)
    assert verify.detect_check(tmp_path)[0] == str(py)


def test_a_node_project_with_a_real_test_script(tmp_path):
    touch(tmp_path / "package.json", '{"scripts": {"test": "vitest run"}}', 1)
    assert verify.detect_check(tmp_path) == ["npm", "test", "--silent"]


def test_the_npm_placeholder_test_script_is_not_a_check(tmp_path):
    touch(tmp_path / "package.json", '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}', 1)
    assert verify.detect_check(tmp_path) is None


def test_go_and_rust_projects(tmp_path):
    go, rust = tmp_path / "g", tmp_path / "r"
    touch(go / "go.mod", "module x", 1)
    touch(rust / "Cargo.toml", "[package]", 1)
    assert verify.detect_check(go) == ["go", "test", "./..."]
    assert verify.detect_check(rust) == ["cargo", "test", "-q"]


def test_a_folder_with_no_tests_has_no_check(tmp_path):
    touch(tmp_path / "notes.md", "hi", 1)
    assert verify.detect_check(tmp_path) is None


@pytest.mark.parametrize("text,expected", [
    ("pytest -q", ["pytest", "-q"]),
    ("npm run lint", ["npm", "run", "lint"]),
    ('python -m pytest -k "a b"', ["python", "-m", "pytest", "-k", "a b"]),
    ("", None), ("   ", None),
    ("make test; rm -rf ~", None), ("pytest | tee x", None), ("pytest && echo hi", None), ("pytest $(whoami)", None),
    ("pytest 'unclosed", None),
])
def test_a_configured_check_is_split_without_a_shell(text, expected):
    assert verify.parse_check(text) == expected


async def test_a_passing_check(tmp_path):
    result = await verify.run_check(tmp_path, [sys.executable, "-c", "print('1 passed in 0.01s')"], timeout_s=20)
    assert result.ran and result.passed is True and "1 passed" in result.summary


async def test_a_failing_check_keeps_the_end_of_its_output(tmp_path):
    code = "print('x'*5000); print('FAILED test_a - assert 1 == 2'); print('1 failed in 0.02s'); raise SystemExit(1)"
    result = await verify.run_check(tmp_path, [sys.executable, "-c", code], timeout_s=20)
    assert result.ran and result.passed is False
    assert result.summary == "1 failed in 0.02s" and "FAILED test_a" in result.tail


async def test_a_missing_check_program_is_not_a_failure_of_the_code(tmp_path):
    result = await verify.run_check(tmp_path, ["definitely-not-installed-xyz"], timeout_s=5)
    assert result.ran is False and result.passed is None and "not installed" in result.summary


async def test_a_check_that_hangs_is_a_failed_check(tmp_path):
    result = await verify.run_check(tmp_path, [sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=0.5)
    assert result.ran and result.passed is False and "timed out" in result.summary


async def test_a_check_gets_no_keys_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_API_KEY", "sk-should-not-leak")
    result = await verify.run_check(tmp_path, [sys.executable, "-c", "import os; print(os.environ.get('SECRET_API_KEY', 'none'))"],
                                    timeout_s=20)
    assert "none" in result.tail and "sk-should" not in result.tail


def check(passed, ran=True):
    return verify.CheckResult(ran=ran, passed=passed, summary="2 passed" if passed else "1 failed", tail="")


@pytest.mark.parametrize("mode,failed,files,chk,verdict,done", [
    ("work", False, ["a.py"], check(True), "verified", True),
    ("work", False, ["a.py"], check(False), "checks_failed", False),
    ("work", False, ["a.py"], None, "unchecked", True),
    ("work", False, ["a.py"], check(None, ran=False), "unchecked", True),
    ("work", False, [], check(True), "no_changes", False),
    ("work", False, [], None, "no_changes", False),
    ("work", True, ["a.py"], check(True), "failed", False),
    ("ask", False, [], None, "answered", True),
    ("ask", False, ["a.py"], None, "changed_in_ask", False),
])
def test_the_verdict_is_decided_from_evidence_not_from_the_agents_words(mode, failed, files, chk, verdict, done):
    out = verify.judge(mode, failed, files, chk)
    assert (out.verdict, out.ok) == (verdict, done)


def test_a_verdict_says_in_words_what_was_and_was_not_shown():
    sentence = lambda *a: verify.judge(*a).sentence  # noqa: E731
    assert "2 files changed" in sentence("work", False, ["a.py", "b.py"], check(True)) and "tests passed" in sentence("work", False, ["a.py", "b.py"], check(True))
    assert "could not confirm" in sentence("work", False, ["a.py"], None)
    assert "no files changed" in sentence("work", False, [], None).lower()
    assert "tests failed" in sentence("work", False, ["a.py"], check(False))


def test_failing_the_same_way_before_and_after_is_not_a_failure_of_the_change():
    before = verify.CheckResult(True, False, "1 failed in 0.02s", "ModuleNotFoundError: app\n1 failed in 0.02s")
    after = verify.CheckResult(True, False, "1 failed in 0.31s", "ModuleNotFoundError: app\n1 failed in 0.31s")
    out = verify.judge("work", False, ["a.py"], after, before=before)
    assert (out.verdict, out.ok) == ("already_failing", False) and "already failing" in out.sentence
    other = verify.CheckResult(True, False, "3 failed in 0.3s", "FAILED test_b\n3 failed in 0.3s")
    assert verify.judge("work", False, ["a.py"], other, before=before).verdict == "checks_failed"
    assert verify.judge("work", False, ["a.py"], after).verdict == "checks_failed"  # no starting point known


def test_failures_that_got_fixed_or_broke_newly_are_judged_normally():
    failing = verify.CheckResult(True, False, "1 failed", "1 failed")
    passing = verify.CheckResult(True, True, "1 passed", "1 passed")
    assert verify.judge("work", False, ["a.py"], passing, before=failing).verdict == "verified"
    assert verify.judge("work", False, ["a.py"], failing, before=passing).verdict == "checks_failed"


def test_a_project_can_name_its_own_check_in_a_file(tmp_path):
    (tmp_path / ".minty").mkdir()
    (tmp_path / ".minty" / "check").write_text("python3 check_video.py out\n")
    assert verify.project_check(tmp_path) == ["python3", "check_video.py", "out"]
    (tmp_path / ".minty" / "check").write_text("make test; rm -rf ~")
    assert verify.project_check(tmp_path) is None  # shell syntax is never run
    (tmp_path / ".minty" / "check").write_text("")
    assert verify.project_check(tmp_path) is None
    assert verify.project_check(tmp_path / "nowhere") is None


def test_an_agent_that_changed_its_own_check_gets_no_credit_for_passing_it():
    passing = verify.CheckResult(True, True, "OK")
    out = verify.judge("work", False, ["a.txt", "check_video.py"], passing, edited_check=["check_video.py"])
    assert (out.verdict, out.ok) == ("changed_own_check", False) and "check_video.py" in out.sentence and "proves nothing" in out.sentence
    assert verify.judge("work", False, ["a.txt"], passing, edited_check=[]).verdict == "verified"
