#!/usr/bin/env python3
"""Look for anything that must never be published: key-shaped strings, the keys you actually use, and personal words.

  python scripts/leak_check.py                                  # key shapes in every tracked file outside tests/
  python scripts/leak_check.py --secrets ~/.config/minty/secrets.toml --words my-words.txt

* Key shapes (a Groq, OpenAI, Google or GitHub key, a private key and so on) are looked for outside tests/ (test fixtures use fake keys).
* `--secrets` is a TOML file of your real keys: every value in it is searched for in EVERY tracked file, tests included. Values are never printed.
* `--words` is a text file, one word or phrase per line (your name, your school, your address, old project names): searched everywhere, ignoring case.
Both files stay outside the repository. Only tracked files (git ls-files) are read. Exit status 1 when something is found.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

MIN_KNOWN = 8  # a stored value shorter than this is too short to search for


def _tracked(root: Path) -> list[str]:
    done = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True, check=True)
    return [p for p in done.stdout.split("\0") if p]


def find_leaks(root: Path, known_values=(), words=()) -> list[str]:
    from minty_core.secretscan import SecretScanner

    root = Path(root)
    scanner = SecretScanner()
    known = [v for v in known_values if isinstance(v, str) and len(v) >= MIN_KNOWN]
    wanted = [w.strip().lower() for w in words if w and w.strip()]
    found: list[str] = []
    for name in _tracked(root):
        try:
            text = (root / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(value in text for value in known):
            found.append(f"{name}: contains a stored key")
        lowered = text.lower()
        for word in wanted:
            if word in lowered:
                found.append(f"{name}: contains a personal word ({word!r})")
        if name.startswith("tests/"):
            continue
        labels = sorted({f.label for f in scanner.scan(text) if f.severity in ("secret", "alarm")})
        found += [f"{name}: looks like {label}" for label in labels]
    return found


def _values(path: Path) -> list[str]:
    from minty_core._toml import tomllib

    def leaves(node):
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict):
            for item in node.values():
                yield from leaves(item)
        elif isinstance(node, list):
            for item in node:
                yield from leaves(item)

    with Path(path).expanduser().open("rb") as f:
        return list(leaves(tomllib.load(f)))


def main(argv: list[str]) -> int:
    secrets, words = None, None
    args = list(argv)
    while args:
        flag = args.pop(0)
        if flag == "--secrets" and args:
            secrets = Path(args.pop(0))
        elif flag == "--words" and args:
            words = Path(args.pop(0)).expanduser().read_text(encoding="utf-8").splitlines()
        else:
            print(__doc__)
            return 2
    found = find_leaks(ROOT, _values(secrets) if secrets else (), words or ())
    if found:
        print("Not clean:\n- " + "\n- ".join(found))
        return 1
    print("Clean: no keys, stored keys or personal words in the tracked files.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
