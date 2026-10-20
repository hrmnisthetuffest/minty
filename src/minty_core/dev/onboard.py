"""Repository onboarding summary (646): what the project is, its layout, how to run and test it, and its recent history, from
the files and git alone (nothing is run, nothing is sent anywhere). README text is outside content, so the tool tags it."""
from __future__ import annotations

import json
import re
from minty_core._toml import tomllib
from collections import Counter
from pathlib import Path

LANGS = {".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".go": "Go", ".rs": "Rust",
         ".java": "Java", ".kt": "Kotlin", ".c": "C", ".h": "C", ".cpp": "C++", ".cs": "C#", ".rb": "Ruby", ".php": "PHP",
         ".sh": "Shell", ".html": "HTML", ".css": "CSS", ".swift": "Swift", ".lua": "Lua"}
_CREDS = re.compile(r"(://)[^/@\s]+@")


def scrub_url(url: str) -> str:
    return _CREDS.sub(r"\1", str(url).strip())


def readme_summary(path: Path, limit: int = 220) -> str:
    for name in ("README.md", "README.rst", "README.txt", "README"):
        f = path / name
        if f.is_file():
            paragraph: list[str] = []
            for raw in f.read_text(encoding="utf-8", errors="replace").splitlines()[:80]:
                line = raw.strip()
                if not line or line.startswith(("#", "![", "[![", "<", "=", "-" * 3, "```")):
                    if paragraph:
                        break
                    continue
                paragraph.append(line)
            return " ".join(paragraph)[:limit]
    return ""


def _toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def describe_project(path: Path) -> tuple[str, list[str], str]:
    """(name, commands, test command) from the project's own files."""
    name, commands, test = path.name, [], ""
    py = _toml(path / "pyproject.toml")
    if py:
        proj = py.get("project", {})
        name = proj.get("name") or name
        commands += [f"{k} ({v})" for k, v in (proj.get("scripts") or {}).items()]
        deps = " ".join(proj.get("dependencies", []) + [d for g in (proj.get("optional-dependencies") or {}).values()
                                                        for d in g])
        if "pytest" in py.get("tool", {}) or "pytest" in deps or (path / "tests").is_dir():
            test = "pytest"
    pkg = path / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        name = data.get("name") or name
        if isinstance(data.get("bin"), dict):
            commands += [f"{k} ({v})" for k, v in data["bin"].items()]
        if "test" in (data.get("scripts") or {}):
            test = test or "npm test"
    cargo = _toml(path / "Cargo.toml")
    if cargo:
        name = cargo.get("package", {}).get("name") or name
        test = test or "cargo test"
    if (path / "go.mod").is_file():
        first = (path / "go.mod").read_text(errors="replace").splitlines()[:1]
        name = first[0].replace("module", "").strip() if first else name
        test = test or "go test ./..."
    make = path / "Makefile"
    if make.is_file() and re.search(r"^test:", make.read_text(errors="replace"), re.M):
        test = test or "make test"
    return name, commands, test


async def onboarding(path: Path, git) -> str:
    path = Path(path)
    probe = await git.run(path, "rev-parse", "--is-inside-work-tree")
    if not probe.ok:
        return f"{path.name} is not a git repository."
    name, commands, test = describe_project(path)
    files = [f for f in (await git.run(path, "ls-files")).out.splitlines() if f]
    langs = Counter(LANGS[Path(f).suffix] for f in files if Path(f).suffix in LANGS)
    top = Counter(f.split("/")[0] + "/" for f in files if "/" in f)
    summary = readme_summary(path)
    lines = [f"{name}: {summary}" if summary else f"{name}: no README summary."]
    if langs:
        lines.append("Languages: " + ", ".join(f"{k} ({v} file{'s' * (v != 1)})" for k, v in langs.most_common(3)) + ".")
    if top:
        lines.append("Layout: " + ", ".join(f"{k} ({v} file{'s' * (v != 1)})" for k, v in top.most_common(5)) + ".")
    bits = []
    if commands:
        bits.append("Commands: " + ", ".join(commands[:4]) + ".")
    if test:
        bits.append(f"To run the tests: {test}.")
    if bits:
        lines.append(" ".join(bits))
    count = (await git.run(path, "rev-list", "--count", "HEAD")).out.strip() or "0"
    branch = (await git.run(path, "rev-parse", "--abbrev-ref", "HEAD")).out.strip()
    last = (await git.run(path, "log", "-n", "1", "--format=%s|%ar")).out.strip()
    remote = scrub_url((await git.run(path, "remote", "get-url", "origin")).out)
    hist = f"History: {count} commit{'s' * (count != '1')} on branch {branch}"
    if last:
        subject, _, when = last.rpartition("|")
        hist += f', latest "{subject[:80]}" ({when})'
    lines.append(hist + (f". Remote: {remote}." if remote else "."))
    return "\n".join(lines)
