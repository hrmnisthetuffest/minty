"""Run a project's tests and say what failed in at most five lines (648). Running tests runs the project's own code, so the
tool asks first and only works inside your projects. Only a fixed set of test commands exists; the only things you can add
are a `-k` expression and a relative test path, both strictly checked."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from minty_core.system.runner import Runner

COMMANDS = {"pytest": ["pytest", "-q", "--tb=line", "-p", "no:cacheprovider"], "npm test": ["npm", "test", "--silent"],
            "go test ./...": ["go", "test", "./..."], "cargo test": ["cargo", "test", "--quiet"],
            "make test": ["make", "test"]}
_EXPR = re.compile(r"[A-Za-z0-9_ .:\[\]()/-]{1,80}")
_PATH = re.compile(r"[A-Za-z0-9_./-]{1,100}")


def _safe_path(p: str) -> bool:
    return bool(_PATH.fullmatch(p)) and not p.startswith(("-", "/")) and ".." not in p.split("/")


def test_rule(argv: list[str]) -> bool:
    for base in COMMANDS.values():
        if argv[:len(base)] == base and argv[0] == base[0]:
            rest = argv[len(base):]
            if base[0] != "pytest":
                return not rest
            if rest[:1] == ["-k"]:
                if len(rest) < 2 or not _EXPR.fullmatch(rest[1]) or rest[1].startswith("-"):
                    return False
                rest = rest[2:]
            return not rest or (len(rest) == 1 and _safe_path(rest[0]))
    return False


def make_runner(timeout: float = 300.0) -> Runner:
    return Runner(allowed={b[0] for b in COMMANDS.values()}, rules={b[0]: test_rule for b in COMMANDS.values()},
                  timeout=timeout, max_out=200_000)


@dataclass
class TestResult:
    __test__ = False  # not a pytest class
    passed: int = 0
    failed: int = 0
    errors: int = 0
    seconds: float | None = None
    failures: list[tuple[str, str]] = field(default_factory=list)

    @property
    def parsed(self) -> bool:
        return bool(self.passed or self.failed or self.errors)


def parse_pytest(out: str) -> TestResult:
    r = TestResult()
    for line in str(out).splitlines():
        m = re.match(r"^(FAILED|ERROR) (\S+)(?: - (.*))?$", line.strip())
        if m:
            r.failures.append((m.group(2), (m.group(3) or "").strip()))
    last = next((ln for ln in reversed(str(out).splitlines())
                 if re.search(r"\d+ (?:failed|passed|errors?)\b.* in [\d.]+s", ln)), "")
    for n, what in re.findall(r"(\d+) (failed|passed|errors?)\b", last):
        if what == "failed":
            r.failed = int(n)
        elif what == "passed":
            r.passed = int(n)
        else:
            r.errors = int(n)
    t = re.search(r" in ([\d.]+)s", last)
    r.seconds = float(t.group(1)) if t else None
    return r


def summarise(r: TestResult, exit_code: int, tail: str = "") -> str:
    if not r.parsed:
        lines = [ln for ln in tail.splitlines() if ln.strip()][-3:]
        return "\n".join([f"The tests could not run (exit code {exit_code}).", *[ln[:140] for ln in lines]])
    secs = f" in {r.seconds:.1f}s" if r.seconds is not None else ""
    if not r.failed and not r.errors:
        return f"All {r.passed} test{'s' * (r.passed != 1)} passed{secs}."
    bits = [f"{r.failed} failed"] if r.failed else []
    bits += [f"{r.errors} error{'s' * (r.errors != 1)}"] if r.errors else []
    bits += [f"{r.passed} passed"] if r.passed else []
    lines = [", ".join(bits) + secs + "."]
    for test, why in r.failures[:3]:
        lines.append(f"{test} — {why[:100]}" if why else test)
    if len(r.failures) > 3:
        lines.append(f"…and {len(r.failures) - 3} more failure{'s' * (len(r.failures) - 3 != 1)}.")
    return "\n".join(lines)


def _generic(out: str, exit_code: int) -> TestResult:
    r = TestResult()
    for line in out.splitlines():
        m = re.match(r"^--- FAIL: (\S+)", line.strip()) or re.match(r"^test (\S+) \.\.\. FAILED", line.strip())
        if m:
            r.failed += 1
            r.failures.append((m.group(1), ""))
    return r


async def run_tests(project: Path, runner, command: str, k: str | None = None, path: str | None = None,
                    timeout: float = 300.0) -> str:
    base = COMMANDS.get(" ".join(str(command).split()))
    if base is None:
        raise ValueError("I only run: " + ", ".join(COMMANDS) + ".")
    argv = list(base)
    if k:
        if base[0] != "pytest" or not _EXPR.fullmatch(k) or k.startswith("-"):
            raise ValueError("That filter is not allowed.")
        argv += ["-k", k]
    if path:
        if base[0] != "pytest" or not _safe_path(path):
            raise ValueError("That test path is not allowed.")
        argv.append(path)
    res = await runner.run(argv, timeout=timeout, cwd=str(project))
    if res.missing:
        return f"{base[0]} is not installed here."
    if res.timed_out:
        return f"The tests took longer than {int(timeout)} seconds, so I stopped them."
    text = res.out + "\n" + res.err
    parsed = parse_pytest(text) if base[0] == "pytest" else _generic(text, res.code)
    if base[0] != "pytest" and res.code == 0:
        return "The tests passed."
    return summarise(parsed, res.code, tail=text)
