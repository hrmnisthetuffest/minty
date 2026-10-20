#!/usr/bin/env python3
"""Replay a recorded day's requests through the current build and compare with that day.

  .venv/bin/python scripts/replay_day.py --day 2026-10-11 --fake            # stand-in model: costs nothing
  .venv/bin/python scripts/replay_day.py --day 2026-10-11 --real --yes      # your providers: spends free quota
Options: --scrub hides e-mails, phone numbers, addresses and --names in the report; --include-private adds the
private turns (left out by default); --out report.json.
"""
import argparse
import asyncio
import json
import logging
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main(argv=None) -> int:
    from minty_core.paths import config_dir
    from minty_core.pseudonym import Pseudonymizer
    from minty_core.replay import load_day, replay_fake, replay_real, summarise_replay

    p = argparse.ArgumentParser()
    p.add_argument("--day", required=True)
    p.add_argument("--config", type=Path, default=None, help="Minty's config folder (default: the usual one)")
    p.add_argument("--fake", action="store_true", help="use a stand-in model (no quota spent)")
    p.add_argument("--real", action="store_true", help="use your real providers (spends free quota)")
    p.add_argument("--yes", action="store_true", help="confirm --real")
    p.add_argument("--scrub", action="store_true")
    p.add_argument("--names", default="", help="comma-separated names to hide with --scrub")
    p.add_argument("--include-private", action="store_true")
    p.add_argument("--out", type=Path)
    args = p.parse_args(argv)
    if not args.fake and not (args.real and args.yes):
        print("choose --fake (stand-in model, free) or --real --yes (spends free quota)", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.WARNING)
    cfg = args.config or config_dir()
    names = [n for n in args.names.split(",") if n.strip()]
    turns = load_day(cfg, args.day, include_private=args.include_private, scrub=args.scrub, names=names)
    with tempfile.TemporaryDirectory() as work:
        if args.fake:
            rows = asyncio.run(replay_fake(turns, Path(work)))
        else:
            rows = asyncio.run(replay_real(turns, cfg, Path(work)))
    if args.scrub:
        masker = Pseudonymizer(names)
        for row in rows:
            row["reply"] = masker.mask(row["reply"])
    report = {"day": args.day, "mode": "fake" if args.fake else "real", "summary": summarise_replay(rows),
              "rows": rows}
    if args.out:
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
