#!/usr/bin/env python3
"""A staged update, never automatic.

  staged_update.py prepare    before you pull: remember this commit, copy the settings, freeze the package versions
  staged_update.py check      after you pulled: full tests + doctor + package drift; prints the rollback on failure
  staged_update.py rollback   go back to the commit `prepare` recorded (asks first)
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from minty_core import updates  # noqa: E402
from minty_core.paths import config_dir  # noqa: E402


def main(argv: list[str]) -> int:
    action = argv[0] if argv else "check"
    directory = config_dir()
    if action == "prepare":
        state = updates.prepare(ROOT, directory)
        print(f"Recorded {state['commit'][:10]}" + ("" if state["clean"] else " (the working tree has uncommitted changes)"))
        return 0
    if action == "check":
        result = updates.check(ROOT, directory)
        print("\n".join(result.steps))
        if not result.ok:
            print(f"\nNot good. To go back: {result.rollback}")
            return 1
        print("\nGood. Restart Minty to use it.")
        return 0
    if action == "rollback":
        state = json.loads((directory / "update-state" / "last-good.json").read_text())
        if input(f"Go back to {state['commit'][:10]}? [y/N] ").lower().startswith("y"):
            return subprocess.call(["git", "checkout", state["commit"]], cwd=ROOT)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
