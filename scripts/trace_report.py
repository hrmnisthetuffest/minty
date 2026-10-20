#!/usr/bin/env python3
"""Print a summary of Minty's traces: ./scripts/trace_report.py [--day YYYY-MM-DD | --last N]"""
import argparse
import datetime as dt
import os
from pathlib import Path

from minty_core.trace import read_events
from minty_core.tracereport import format_report, summarise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", help="one day, YYYY-MM-DD (default today)")
    parser.add_argument("--last", type=int, default=1, help="number of days ending today")
    parser.add_argument("--dir", default=os.path.expanduser("~/.config/minty/traces"))
    args = parser.parse_args(argv)
    days = [args.day] if args.day else [
        (dt.date.today() - dt.timedelta(days=i)).isoformat() for i in range(args.last - 1, -1, -1)]
    events = [e for d in days for e in read_events(Path(args.dir), d)]
    print(f"Traces for {days[0]}" + (f" → {days[-1]}" if len(days) > 1 else ""))
    print(format_report(summarise(events)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
