"""Did the coding agent really do the work? Evidence from the disk and from the project's own tests, never its own words.

An agent that exits cleanly and says "done" has only made a claim. Before the job and after it, the folder is
fingerprinted (so "nothing changed" and "these files changed" are facts); the project's tests are then run without any
keys in their environment; and `judge` turns those facts into one verdict and one honest sentence.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from minty_core import shellkit

IGNORED_DIRS = frozenset({".git", "__pycache__", "node_modules", ".venv", "venv", ".pytest_cache", ".mypy_cache",
                          ".ruff_cache", ".tox"})
FILE_LIMIT = 20_000
TAIL_CHARS = 1500
SUMMARY_CHARS = 120
NPM_PLACEHOLDER = "no test specified"


@dataclass
class Fingerprint:
    files: dict[str, tuple[int, int]] = field(default_factory=dict)  # relative path -> (size, mtime_ns)
    truncated: bool = False  # the folder had more files than were looked at


def fingerprint(root: Path, limit: int = FILE_LIMIT) -> Fingerprint:
    root = Path(root)
    out, stack = Fingerprint(), [root]
    while stack:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in IGNORED_DIRS:
                        stack.append(Path(entry.path))
                    continue
                if len(out.files) >= limit:
                    out.truncated = True
                    return out
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            out.files[os.path.relpath(entry.path, root)] = (st.st_size, st.st_mtime_ns)
    return out


def changed(before: Fingerprint, after: Fingerprint) -> list[str]:
    paths = set(before.files) | set(after.files)
    return sorted(p for p in paths if before.files.get(p) != after.files.get(p))


# ---- which tests to run --------------------------------------------------------------------------------------------
def _python_check(project: Path) -> list[str] | None:
    has_tests = any((project / d).is_dir() for d in ("tests", "test")) or (project / "pytest.ini").exists() or \
        any(project.glob("test_*.py"))
    pyproject = project / "pyproject.toml"
    if not has_tests and pyproject.exists():
        try:
            has_tests = "pytest" in pyproject.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    if not has_tests:
        return None
    for venv in (".venv", "venv"):
        python = project / venv / "bin" / "python"
        if python.is_file() and os.access(python, os.X_OK):
            return [str(python), "-m", "pytest", "-q", "-x"]
    return ["python3", "-m", "pytest", "-q", "-x"]


def _node_check(project: Path) -> list[str] | None:
    try:
        script = json.loads((project / "package.json").read_text(encoding="utf-8")).get("scripts", {}).get("test")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(script, str) or not script.strip() or NPM_PLACEHOLDER in script:
        return None
    return ["npm", "test", "--silent"]


def project_check(project: Path) -> list[str] | None:
    """The check a project names itself in `.minty/check` (one plain command, no shell). Lets any kind of project, a video studio
    as much as a Python package, say what "it works" means."""
    try:
        text = (Path(project) / ".minty" / "check").read_text(encoding="utf-8")
    except OSError:
        return None
    return parse_check(text.strip())


def detect_check(project: Path) -> list[str] | None:
    """The command that tests this project, found from its files; None when there is nothing to run."""
    project = Path(project)
    if (check := project_check(project)) is not None:
        return check
    if (check := _python_check(project)) is not None:
        return check
    if (project / "package.json").exists() and (check := _node_check(project)) is not None:
        return check
    if (project / "go.mod").exists():
        return ["go", "test", "./..."]
    if (project / "Cargo.toml").exists():
        return ["cargo", "test", "-q"]
    return None


def parse_check(command: str) -> list[str] | None:
    """A check command from the settings, as an argument list. No shell: anything a shell would act on is refused."""
    if not isinstance(command, str) or not command.strip() or len(command) > shellkit.MAX_COMMAND_CHARS:
        return None
    if shellkit.has_metachars(command):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    return words or None


# ---- running them ---------------------------------------------------------------------------------------------------
@dataclass
class CheckResult:
    ran: bool  # False: the program was not there, so nothing was learned about the code
    passed: bool | None  # None when it did not run
    summary: str = ""
    tail: str = ""


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:SUMMARY_CHARS] if lines else ""


async def run_check(project: Path, argv: list[str], timeout_s: float = 300.0) -> CheckResult:
    result = await shellkit.run(argv, cwd=str(project), timeout_s=timeout_s, max_chars=4000)
    if result.missing:
        return CheckResult(False, None, f"{argv[0]} is not installed")
    tail = result.text[-TAIL_CHARS:]
    if result.timed_out:
        return CheckResult(True, False, f"timed out after {int(timeout_s)} seconds", tail)
    if result.too_much:
        return CheckResult(True, False, "printed far too much output and was stopped", tail)
    summary = _last_line(result.text) or f"exited with code {result.code}"
    return CheckResult(True, result.code == 0, summary, tail)


# ---- the verdict ----------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Verdict:
    verdict: str  # verified | unchecked | answered | checks_failed | no_changes | failed | changed_in_ask
    ok: bool  # the job counts as done
    sentence: str


def describe_files(files: list[str]) -> str:
    n = len(files)
    shown = ", ".join(files[:3]) + (", …" if n > 3 else "")
    return f"{n} file{'s' * (n != 1)} changed ({shown})"


_DURATIONS = re.compile(r"\bin \d+(?:\.\d+)?s\b|\b\d+(?:\.\d+)?s\b|\(\d+:\d+:\d+\)|0x[0-9a-f]+")


def _signature(check: CheckResult) -> str:
    """The failure with the timings taken out, so the same failure on two runs compares equal."""
    return " ".join(_DURATIONS.sub("", check.tail or check.summary).split())[-500:]


def same_failure(before: CheckResult | None, after: CheckResult | None) -> bool:
    return bool(before and after and before.ran and after.ran and before.passed is False and after.passed is False
                and _signature(before) == _signature(after))


def judge(mode: str, agent_failed: bool, files: list[str], check: CheckResult | None,
          before: CheckResult | None = None, edited_check: list[str] | None = None) -> Verdict:
    if agent_failed:
        return Verdict("failed", False, "The agent reported an error.")
    if mode == "ask":
        if files:
            return Verdict("changed_in_ask", False, f"It was asked only to look, but {describe_files(files)}.")
        return Verdict("answered", True, "Answered; nothing was changed.")
    if not files:
        return Verdict("no_changes", False, "It reports it is done, but no files changed.")
    if edited_check:  # it changed the thing that grades it: a pass says nothing
        return Verdict("changed_own_check", False, f"{describe_files(files)}, but it also changed the check itself "
                       f"({', '.join(edited_check)}), so passing it proves nothing.")
    if same_failure(before, check):  # nothing the agent did changed the result: the tests, or the setup, were the problem
        return Verdict("already_failing", False, f"{describe_files(files)}, but the tests were already failing before it started "
                       f"and fail the same way now ({check.summary}), so nothing shows the change works.")
    if check is not None and check.ran and check.passed is False:
        return Verdict("checks_failed", False, f"{describe_files(files)}, but the tests failed ({check.summary}).")
    if check is not None and check.ran and check.passed:
        return Verdict("verified", True, f"{describe_files(files)} and the tests passed ({check.summary}).")
    why = "I found no tests to run" if check is None else check.summary
    return Verdict("unchecked", True, f"{describe_files(files)}. I could not confirm it works: {why}.")
