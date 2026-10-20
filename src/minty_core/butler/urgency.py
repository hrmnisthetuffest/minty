"""Urgency-aware interruptions (971): only what is truly time-sensitive interrupts; the rest waits for a digest.

now    — said at once (and not held back by focus mode): a time you gave has come, a renewal is due, a check-in
today  — one line in the next digest (morning and evening by default)
later  — one line in the Sunday digest
At most `max_now` a day interrupt; the extras join the digest."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from minty_core.fsutil import atomic_write_text

HOUR = 3600


def classify(notice, now: float) -> str:
    if notice.domain == "checkin":
        return "now"
    if notice.lead is not None:
        return "now" if notice.lead <= 1 else "today" if notice.lead <= 7 else "later"
    if notice.domain == "documents":
        return "now"
    due = datetime.fromtimestamp(notice.due)
    explicit = (due.hour, due.minute) != (9, 0)  # you said a time, not just a day
    if explicit and abs(now - notice.due) <= 3 * HOUR:
        return "now"
    return "today"


class Digest:
    """Lines waiting for the next digest, plus today's interruption count. A small owner-only file."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path, self._clock = Path(path), clock

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        atomic_write_text(self.path, json.dumps(data), mode=0o600)

    def _day(self) -> str:
        return datetime.fromtimestamp(self._clock()).strftime("%Y-%m-%d")

    def add(self, text: str, level: str) -> None:
        data = self._load()
        lines = data.setdefault(level, [])
        if text not in lines:
            lines.append(text)
            self._save(data)

    def flush(self, level: str) -> list[str]:
        data = self._load()
        lines = data.pop(level, [])
        if lines:
            self._save(data)
        return lines

    def now_count(self) -> int:
        data = self._load()
        return int(data.get("now_count", 0)) if data.get("now_day") == self._day() else 0

    def bump_now(self) -> None:
        data = self._load()
        data["now_day"] = self._day()
        data["now_count"] = self.now_count() + 1
        self._save(data)

    def slots_done(self) -> list[str]:
        data = self._load()
        return list(data.get("slots", [])) if data.get("slots_day") == self._day() else []

    def mark_slot(self, slot: str) -> None:
        data = self._load()
        data["slots"] = self.slots_done() + [slot]
        data["slots_day"] = self._day()
        self._save(data)


class Interruptions:
    def __init__(self, digest: Digest, max_now: int = 3, clock: Callable[[], float] = time.time):
        self.digest, self.max_now, self._clock = digest, max_now, clock

    def route(self, notice) -> str:
        level = classify(notice, self._clock())
        if level == "now":
            if self.digest.now_count() >= self.max_now:
                return "today"
            self.digest.bump_now()
        return level
