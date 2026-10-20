"""Resurfacing (606): "on this day" and spaced recall for the notes you choose to review."""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from minty_core.fsutil import atomic_write_text
from minty_core.vault.note import Note

INTERVALS = (1, 3, 7, 21, 60, 180)  # days until the next look after each remembered answer


class Resurfacer:
    def __init__(self, vault, state: Path, clock: Callable[[], float] = time.time):
        self.vault, self._state, self._clock = vault, Path(state), clock

    def _load(self) -> dict:
        try:
            return json.loads(self._state.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        atomic_write_text(self._state, json.dumps(data))

    def _today(self, today: date | None) -> date:
        return today or datetime.fromtimestamp(self._clock()).date()

    # ---- on this day ---------------------------------------------------------------------------------
    def on_this_day(self, today: date | None = None, limit: int = 3) -> list[Note]:
        today = self._today(today)
        out = []
        for n in self.vault.list(None, limit=2000):
            try:
                made = datetime.fromisoformat(n.created).date()
            except ValueError:
                continue
            if (n.confidence != "untrusted" and made.year < today.year
                    and (made.month, made.day) == (today.month, today.day)):
                out.append(n)
        return out[:limit]

    # ---- spaced recall -------------------------------------------------------------------------------
    def add_to_review(self, ident: str, today: date | None = None) -> Note:
        today = self._today(today)
        note = self.vault.get(ident)
        if note is None:
            raise ValueError("No note with that id.")
        if note.confidence == "untrusted":
            raise ValueError("That note came from outside; approve it into your notes first.")
        if "review" not in note.tags:
            note = self.vault.update(ident, tags=[*note.tags, "review"])
        data = self._load()
        data[ident] = {"step": 0, "due": (today + timedelta(days=INTERVALS[0])).isoformat()}
        self._save(data)
        return note

    def due(self, today: date | None = None) -> list[Note]:
        today = self._today(today)
        out = []
        for ident, s in self._load().items():
            try:
                if date.fromisoformat(s["due"]) > today:
                    continue
            except (KeyError, ValueError):
                continue
            n = self.vault.get(ident)
            if n is not None and not n.path.startswith("_archive/") and n.confidence != "untrusted":
                out.append(n)
        return out

    def mark(self, ident: str, remembered: bool, today: date | None = None) -> None:
        today = self._today(today)
        data = self._load()
        s = data.get(ident)
        if s is None:
            return
        step = min(int(s.get("step", 0)) + 1, len(INTERVALS) - 1) if remembered else 0
        data[ident] = {"step": step, "due": (today + timedelta(days=INTERVALS[step])).isoformat()}
        self._save(data)
