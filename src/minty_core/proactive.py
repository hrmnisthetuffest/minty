"""May Minty say this by itself, right now? One small policy: kill switches, a night
window and a rate limit decide between saying it, holding it (shown silently, delivered later as one digest)
and dropping it. It never decides *what* to say."""
from __future__ import annotations

import re
from collections import deque
from datetime import datetime, time, timedelta
from typing import Callable

from minty_core.datewords import parse_hhmm

CATEGORY = {"brief": "brief", "nudge": "due", "digest": "due"}
_OFF = re.compile(r"\b(stop|no more|turn off|disable|don'?t) (the )?(proactive|morning brief|briefing|nudges?|"
                  r"reminders? from you|unprompted)", re.I)
_ON = re.compile(r"\b(turn on|enable|start|resume) (the )?(proactive|morning brief|briefing|nudges?)", re.I)


class ProactivePolicy:
    def __init__(self, cfg: Callable[[], object]):
        self._cfg = cfg
        self._spoken: deque[datetime] = deque(maxlen=120)

    def gate(self, item, now: datetime) -> str:
        cfg = self._cfg()
        category = CATEGORY.get(item.kind, "other")
        if not cfg.enabled or (category == "brief" and not cfg.brief) or (category == "due" and not cfg.due_nudges):
            return "drop"
        if self._night(cfg, now.time()):
            return "hold"
        recent = [t for t in self._spoken if now - t < timedelta(hours=1)]
        return "hold" if len(recent) >= cfg.max_per_hour else "say"

    def spoken(self, now: datetime) -> None:
        self._spoken.append(now)

    @staticmethod
    def _night(cfg, t: time) -> bool:
        start, end = parse_hhmm(cfg.night_from), parse_hhmm(cfg.night_to)
        return start <= t or t < end if start > end else start <= t < end


class ProactiveControl:
    """'stop the morning brief', 'turn proactive back on': switches that live in the config."""

    def __init__(self, store):
        self._store = store

    def handle(self, text: str) -> str | None:
        if len(text.split()) > 10:
            return None
        low = text.lower()
        off, on = _OFF.search(low), _ON.search(low)
        if not (off or on):
            return None
        brief_only = bool(re.search(r"morning brief|briefing", low))
        data = self._store.config.model_dump(mode="json")
        key = "brief" if brief_only else "enabled"
        data["proactive"][key] = bool(on)
        if on and not brief_only:
            data["proactive"]["brief"] = True
        self._store.update(data)
        what = "the morning brief" if brief_only else "things I start by myself"
        return f"Okay, {what} {'back on' if on else 'off'}."
