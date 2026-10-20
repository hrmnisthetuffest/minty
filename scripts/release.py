#!/usr/bin/env python3
"""Check that a release is ready and print the commands that publish it. It publishes nothing itself.

  python scripts/release.py v1.0.1
  python scripts/release.py v1.0.1 --ci      # in the release workflow: the tag and the tree already exist
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def problems(root: Path, tag: str, ci: bool = False) -> list[str]:
    sys.path.insert(0, str(root / "src"))
    from minty_core.selfupdate import parse_version

    found: list[str] = []
    wanted = parse_version(tag)
    version = re.search(r'__version__ = "([^"]+)"', (root / "src/minty_core/__init__.py").read_text())
    if wanted is None:
        return [f"{tag!r} is not a version tag (for example v1.0.0-alpha.20)"]
    if version is None or parse_version(version.group(1)) != wanted:
        found.append(f"the tag {tag} does not match __version__ ({version.group(1) if version else 'missing'})")
    pyproject = re.search(r'^version = "([^"]+)"', (root / "pyproject.toml").read_text(), re.M)
    if pyproject is None or parse_version(pyproject.group(1)) != wanted:
        found.append(f"the tag {tag} does not match pyproject.toml ({pyproject.group(1) if pyproject else 'missing'})")
    if f"## {tag.lstrip('vV')}" not in (root / "CHANGELOG.md").read_text():
        found.append(f"CHANGELOG.md has no heading for {tag.lstrip('vV')}")
    if ci:
        return found
    git = lambda *a: subprocess.run(["git", *a], cwd=root, capture_output=True, text=True)  # noqa: E731
    if git("status", "--porcelain").stdout.strip():
        found.append("the working tree has uncommitted changes")
    if git("rev-parse", "-q", "--verify", f"refs/tags/{tag}").returncode == 0:
        found.append(f"the tag {tag} already exists")
    return found


def main(argv: list[str]) -> int:
    ci = "--ci" in argv
    argv = [a for a in argv if a != "--ci"]
    if len(argv) != 1:
        print(__doc__)
        return 2
    tag = argv[0]
    bad = problems(ROOT, tag, ci=ci)
    if bad:
        print("Not ready:\n- " + "\n- ".join(bad))
        return 1
    if ci:
        print(f"{tag} matches the version and the changelog.")
        return 0
    print(f"Ready. To publish {tag} run:\n\n"
          f"  git tag -a {tag} -m \"Minty {tag.lstrip('vV')}\"      # add -s to sign it\n"
          f"  git push origin main --tags\n\n"
          f"Pushing the tag starts the release workflow (.github/workflows/release.yml), which makes the GitHub release that\n"
          f"`minty update` looks for, with this version's changelog section as its notes.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
