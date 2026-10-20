"""One handle on the study suite for the plugin, the loops and Telegram. Nothing is created on disk until
`study.enabled` is on and something asks for it."""
from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from minty_core.study.cards import Cards
from minty_core.study.db import StudyDB
from minty_core.study.plan import Planner
from minty_core.study.practice import Mistakes, Papers

OFF = "The study tools are switched off (study.enabled on the Switches page)."


class StudyOff(RuntimeError):
    pass


class StudyKit:
    def __init__(self, directory: Path, clock: Callable[[], float]):
        self.db = StudyDB(Path(directory) / "study.db")
        self.cards = Cards(self.db, clock)
        self.papers = Papers(self.db, clock)
        self.mistakes = Mistakes(self.db, self.cards, clock)
        self.current_card: int | None = None  # the card being quizzed, so "got it" grades the right one

    # exams live in the same file
    def add_exam(self, subject: str, day: str, topics: list[str]) -> int:
        import json
        with self.db.lock:
            cur = self.db.db.execute("INSERT INTO exams(subject, day, topics, created) VALUES (?,?,?,?)",
                                     (subject.strip().title(), day, json.dumps(topics[:20]), time.time()))
            self.db.db.commit()
            return cur.lastrowid

    def exams(self) -> list[tuple[str, "object", list[str]]]:
        import json
        from datetime import date
        with self.db.lock:
            rows = self.db.db.execute("SELECT subject, day, topics FROM exams ORDER BY day").fetchall()
        out = []
        for r in rows:
            try:
                out.append((r["subject"], date.fromisoformat(r["day"]), json.loads(r["topics"] or "[]")))
            except ValueError:
                continue
        return out


def summarise_upload(provider, kind: str, text: str, caption: str) -> str | None:
    """A file or photo sent with the caption "summarise": the key sentences, picked locally, no model."""
    if provider is None or not provider.active() or kind not in ("pdf", "image", "text"):
        return None
    if not re.match(r"^\s*(?:please\s+)?summari[sz]e\b", str(caption or ""), re.I):
        return None
    from minty_core.study.text import summarise

    return summarise(text).text()


def due_notices(provider, now: datetime, state: dict, review_text: Callable[[], str]) -> tuple[list[tuple[str, bool]], dict]:
    """Reminders that have come due: (text, urgent) pairs and the state that remembers what was said."""
    from minty_core.study.plan import class_reminders, tomorrow_bag

    cfg = provider._config().study
    if not cfg.enabled:
        return [], state
    out: list[tuple[str, bool]] = []
    day = now.strftime("%Y-%m-%d")
    state = {k: v for k, v in state.items() if k.endswith(day) or k.startswith("review") or k.startswith("bag")}
    if cfg.class_reminders and cfg.timetable:
        for r in class_reminders(cfg.timetable, now, cfg.lead_min):
            key = f"class:{r}:{day}"
            if key not in state:
                state[key] = True
                out.append((r, True))  # a reminder you asked for: it is not held back by focus mode
        if now.hour * 60 + now.minute >= 19 * 60 + 30 and state.get("bag") != day:
            state["bag"] = day
            bag = tomorrow_bag(cfg.timetable, now.date())
            if bag:
                out.append((bag, False))
    if cfg.weekly_review and now.weekday() == 6 and now.hour >= 18 and state.get("review") != day:
        state["review"] = day
        text = review_text()
        if text:
            out.append((text, False))
    return out, state


class StudyProvider:
    def __init__(self, config: Callable, directory: Path, clock: Callable[[], float] = time.time):
        self._config, self._dir, self._clock = config, Path(directory), clock
        self._kit: StudyKit | None = None

    def active(self) -> bool:
        return bool(self._config().study.enabled)

    def kit(self) -> StudyKit:
        if not self._config().study.enabled:
            raise StudyOff(OFF)
        if self._kit is None:
            self._kit = StudyKit(self._dir, self._clock)
        return self._kit

    def planner(self) -> Planner:
        return Planner(self._config())
