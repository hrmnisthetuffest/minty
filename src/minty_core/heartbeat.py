"""Heartbeat and the weekly health digest: tell the person when Minty is unwell, without any outside service.

The heartbeat file is touched every minute (anything that can read a timestamp can watch it). An optional "alive"
message goes out at a chosen time, so a missing one is the signal. The digest is plain words from what Minty already
records: starts and crashes, errors, provider health, jobs, disk and the last backup.
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from minty_core.trace import read_events


class Heartbeat:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path, self._clock = Path(path), clock

    def beat(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"ts": self._clock(), "pid": os.getpid()}), encoding="utf-8")
        tmp.replace(self.path)

    def age(self) -> float | None:
        """Seconds since the last beat, or None if there never was one."""
        try:
            return self._clock() - float(json.loads(self.path.read_text(encoding="utf-8"))["ts"])
        except (OSError, ValueError, KeyError):
            return None


def due_alive(now: datetime, at: str, last_day: str) -> bool:
    """True once a day, at or after the chosen HH:MM (empty = off)."""
    if not at:
        return False
    hour, _, minute = at.partition(":")
    return (now.hour, now.minute) >= (int(hour), int(minute or 0)) and last_day != now.strftime("%Y-%m-%d")


def gather(trace_dir: Path, now: datetime, days: int = 7) -> dict:
    """Counts from the traces of the last `days` days."""
    counts: Counter = Counter()
    kinds: Counter = Counter()
    slowest = 0.0
    for i in range(days):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        for e in read_events(trace_dir, day):
            k = e.get("kind")
            counts[k] += 1
            if k == "llm_call" and e.get("outcome") == "error":
                kinds[e.get("error_kind") or "other"] += 1
            if k == "turn" and e.get("outcome") == "answered":
                counts["answered"] += 1
            if k == "turn" and e.get("first_audio_ms"):
                slowest = max(slowest, float(e["first_audio_ms"]))
            if k == "tool_call" and e.get("ok") is False:
                counts["tool_errors"] += 1
    return {"counts": dict(counts), "model_errors": dict(kinds), "slowest_first_audio_ms": slowest}


def digest(*, trace_dir: Path, now: datetime, starts: int, unclean_starts: int, providers: list[dict],
           disk_free_mb: float | None, last_backup: float | None, interrupted_jobs: int,
           days: int = 7) -> tuple[str, dict]:
    """(spoken digest, the facts it came from)."""
    g = gather(trace_dir, now, days)
    c = g["counts"]
    parts = []
    parts.append(f"In the last {days} days I answered {c.get('answered', 0)} requests, "
                 f"with {unclean_starts} unclean start{'s' * (unclean_starts != 1)} out of {starts}.")
    errors = sum(g["model_errors"].values())
    if errors:
        top = ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in sorted(g["model_errors"].items(),
                                                                          key=lambda kv: -kv[1])[:3])
        parts.append(f"Model calls failed {errors} times ({top}).")
    if c.get("tool_errors"):
        parts.append(f"{c['tool_errors']} tool calls failed.")
    if c.get("loop_stall"):
        parts.append(f"The main loop stalled {c['loop_stall']} times.")
    bad = [p["name"] for p in providers if p.get("health") in ("bad", "down", "demoted") or p.get("cooling_for", 0) > 0]
    if bad:
        parts.append("Providers having trouble: " + ", ".join(bad) + ".")
    if interrupted_jobs:
        parts.append(f"{interrupted_jobs} background job{'s' * (interrupted_jobs != 1)} were interrupted.")
    if disk_free_mb is not None and disk_free_mb < 2048:
        parts.append(f"Disk space is getting low: {disk_free_mb / 1024:.1f} GB free.")
    if last_backup is None:
        parts.append("I have no backup yet.")
    else:
        age = (now.timestamp() - last_backup) / 86400
        parts.append(f"The last backup was {'today' if age < 1 else f'{int(age)} days ago'}"
                     + (" — that is too old." if age > 3 else "."))
    if len(parts) == 2 and errors == 0:
        parts.append("Everything looks healthy.")
    facts = {**g, "starts": starts, "unclean_starts": unclean_starts, "interrupted_jobs": interrupted_jobs,
             "disk_free_mb": disk_free_mb, "last_backup": last_backup}
    return " ".join(parts), facts
