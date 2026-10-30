"""Regenerate the facts block of MINTY_IDENTITY.md from the code (the same way gen_plugin_docs.py keeps docs/plugins.md current).

    PYTHONPATH=src python scripts/gen_identity.py          # rewrite the block
    PYTHONPATH=src python scripts/gen_identity.py --check  # exit 1 if it is out of date
"""
import sys

from minty_core.mind import identity


def main() -> int:
    text = identity.load()
    if "--check" in sys.argv:
        ok = identity.is_current(text)
        print("MINTY_IDENTITY.md facts are current" if ok else "MINTY_IDENTITY.md facts are out of date: run scripts/gen_identity.py")
        return 0 if ok else 1
    identity.FILE.write_text(identity.with_facts(text), encoding="utf-8")
    print(f"wrote {identity.FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
