"""Timers that survive a restart: due times (wall clock) in a small owner-only JSON file, re-read on every operation so
several users of the file (the plugin after a reload, the overlay's API) agree. One-off or repeating, named, with a
kind ("timer", "study", "focus")."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

MAX_SECONDS = 24 * 3600
MIN_REPEAT_S = 60
LATE_AFTER_S = 5.0
MAX_TIMERS = 50


@dataclass
class Timer:
    id: int
    label: str
    due: float
    repeat_s: float = 0.0
    kind: str = "timer"
    late: bool = False  # set on a timer that came due while nothing was running (not stored)

    def remaining(self, now: float) -> float:
        return max(0.0, self.due - now)


class TimerStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path, self.clock = Path(path), clock

    # ---- file -------------------------------------------------------------------------------------
    def _load(self) -> tuple[int, list[Timer]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            timers = [Timer(int(t["id"]), str(t["label"]), float(t["due"]), float(t.get("repeat_s", 0)),
                            str(t.get("kind", "timer"))) for t in data["timers"]]
            return int(data.get("next_id", 1)), timers
        except FileNotFoundError:
            return 1, []
        except (OSError, ValueError, KeyError, TypeError):
            try:
                self.path.replace(self.path.with_name(self.path.name + ".bad"))  # keep it, start clean
            except OSError:
                pass
            return 1, []

    def _save(self, next_id: int, timers: list[Timer]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"next_id": next_id, "timers": [{"id": t.id, "label": t.label, "due": t.due,
                                                       "repeat_s": t.repeat_s, "kind": t.kind} for t in timers]}, f)
        tmp.replace(self.path)

    # ---- operations ---------------------------------------------------------------------------------
    def all(self) -> list[Timer]:
        return sorted(self._load()[1], key=lambda t: t.due)

    def add(self, seconds: float, label: str = "", repeat_s: float = 0.0, kind: str = "timer") -> Timer:
        if not 0 < seconds <= MAX_SECONDS:
            raise ValueError("seconds must be greater than 0 and at most 86400")
        if repeat_s and not MIN_REPEAT_S <= repeat_s <= MAX_SECONDS:
            raise ValueError("a repeat must be between a minute and a day")
        next_id, timers = self._load()
        if len(timers) >= MAX_TIMERS:
            raise ValueError("too many timers are running")
        t = Timer(next_id, (label or "").strip()[:60] or "timer", self.clock() + seconds, float(repeat_s), kind)
        self._save(next_id + 1, timers + [t])
        return t

    def cancel(self, timer_id: int) -> Timer | None:
        next_id, timers = self._load()
        gone = next((t for t in timers if t.id == timer_id), None)
        if gone is not None:
            self._save(next_id, [t for t in timers if t.id != timer_id])
        return gone

    def fire_due(self) -> list[Timer]:
        """The timers whose time has come (flagged late if they came due long ago); repeating ones are set again."""
        now = self.clock()
        next_id, timers = self._load()
        fired, keep = [], []
        for t in timers:
            if t.due > now:
                keep.append(t)
                continue
            fired.append(Timer(t.id, t.label, t.due, t.repeat_s, t.kind, late=now - t.due > LATE_AFTER_S))
            if t.repeat_s:
                keep.append(Timer(t.id, t.label, now + t.repeat_s, t.repeat_s, t.kind))
        if fired:
            self._save(next_id, keep)
        return fired

    def next_due_in(self) -> float | None:
        timers = self._load()[1]
        return min((t.remaining(self.clock()) for t in timers), default=None)
