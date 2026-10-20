#!/usr/bin/env python3
"""Helpers for releasing Minty: which version number comes next, the changelog's Unreleased section, and release notes.

Used by the maintainer's flow (see docs/releasing.md) and by the release workflow that writes the GitHub release text.

  python scripts/release_tools.py next patch          # prints the next version number
  python scripts/release_tools.py notes v1.0.1        # prints that version's changelog section
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PLAIN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
FIRST_RELEASE = "1.0.1"


def next_version(current: str, bump: str) -> str:
    """"patch" (1.0.1 -> 1.0.2), "minor" (-> 1.1.0), "major" (-> 2.0.0), or "first": the first public release, 1.0.1."""
    if bump not in ("patch", "minor", "major", "first"):
        raise ValueError(f"unknown bump {bump!r}: use patch, minor, major or first")
    plain = PLAIN.match(current.strip())
    if bump == "first":
        if plain is None or current.strip() == "1.0.0":
            return FIRST_RELEASE
        raise ValueError(f"{current} is already released; 'first' is only for the first release")
    if plain is None:
        if re.match(r"^\d+\.\d+\.\d+[-.]?(a|alpha|b|beta|rc)", current.strip(), re.I):
            raise ValueError(f"{current} is a pre-release: make the first release with 'first' (it becomes {FIRST_RELEASE})")
        raise ValueError(f"{current!r} is not a version number")
    major, minor, patch = (int(x) for x in plain.groups())
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major + 1}.0.0"


def set_version(root: Path, version: str) -> None:
    """Write the version into the package and into pyproject.toml (the `version = ...` line of [project] only)."""
    root = Path(root)
    init = root / "src" / "minty_core" / "__init__.py"
    text = init.read_text(encoding="utf-8")
    new, n = re.subn(r'^__version__ = "[^"]*"', f'__version__ = "{version}"', text, count=1, flags=re.M)
    if n != 1:
        raise ValueError("no __version__ in src/minty_core/__init__.py")
    init.write_text(new, encoding="utf-8")
    meta = root / "pyproject.toml"
    text = meta.read_text(encoding="utf-8")
    new, n = re.subn(r'^version = "[^"]*"', f'version = "{version}"', text, count=1, flags=re.M)
    if n != 1:
        raise ValueError("no version line in pyproject.toml")
    meta.write_text(new, encoding="utf-8")


def _sections(text: str) -> list[tuple[int, int, str]]:
    """(start, end, heading) of every "## " section."""
    marks = [(m.start(), m.group(0)) for m in re.finditer(r"^## .*$", text, re.M)]
    return [(start, marks[i + 1][0] if i + 1 < len(marks) else len(text), heading) for i, (start, heading) in enumerate(marks)]


def _unreleased(text: str) -> tuple[int, int] | None:
    for start, end, heading in _sections(text):
        if heading.strip().lower() == "## unreleased":
            return start, end
    return None


def finalize_changelog(text: str, version: str, date: str) -> str:
    """Turn the Unreleased section into the heading of this release."""
    found = _unreleased(text)
    if found is None or not text[found[0]:found[1]].split("\n", 1)[1].strip():
        raise ValueError("there is nothing to release: the changelog's Unreleased section is missing or empty")
    start, _ = found
    line_end = text.index("\n", start)
    return text[:start] + f"## {version} ({date})" + text[line_end:]


def add_entry(text: str, markdown: str) -> str:
    """Append a changelog entry under Unreleased (created at the bottom, where the newest entries go, if it is not there)."""
    markdown = markdown.strip("\n")
    found = _unreleased(text)
    if found is None:
        return text.rstrip("\n") + f"\n\n## Unreleased\n\n{markdown}\n"
    start, end = found
    body = text[start:end].rstrip("\n")
    return text[:start] + body + "\n" + markdown + "\n" + ("\n" + text[end:] if text[end:] else "")


def release_notes(text: str, version: str) -> str:
    """The changelog section of one version, without its heading."""
    wanted = version.lstrip("vV")
    for start, end, heading in _sections(text):
        title = heading[3:].strip()
        if title == wanted or title.startswith(wanted + " "):
            return text[start:end].split("\n", 1)[1].strip()
    raise ValueError(f"CHANGELOG.md has no section for {wanted}")


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parents[1]
    if len(argv) == 2 and argv[0] == "next":
        match = re.search(r'^__version__ = "([^"]+)"', (root / "src/minty_core/__init__.py").read_text(), re.M)
        print(next_version(match.group(1), argv[1]))
        return 0
    if len(argv) == 2 and argv[0] == "notes":
        print(release_notes((root / "CHANGELOG.md").read_text(encoding="utf-8"), argv[1]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
