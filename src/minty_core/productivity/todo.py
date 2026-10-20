"""To-do inbox triage (589): undated tasks get a suggested day. Nothing changes until you apply the suggestions."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from minty_core.butler.when import parse_when

_MUST = re.compile(r"\b(?:submit|pay|form|fee|exam|deadline|register|registration|apply|renew|last date)\b", re.I)
_DO = re.compile(r"\b(?:buy|call|email|text|book|order|return|print|ask|get|pick up|send)\b", re.I)


@dataclass
class Suggestion:
    item_id: int
    name: str
    day: date
    reason: str


def _next_saturday(today: date) -> date:
    return today + timedelta(days=(5 - today.weekday()) % 7)


def triage(butler, today: date) -> list[Suggestion]:
    out = []
    for item in butler.list("tasks"):
        if item.due is not None:
            continue
        said, _every = parse_when(item.name, datetime.combine(today, datetime.min.time().replace(hour=10)))
        if said is not None:
            day, why = datetime.fromtimestamp(said).date(), "you said when"
        elif _MUST.search(item.name):
            day, why = today + timedelta(days=2), "it has a deadline feel: do it soon"
        elif _DO.search(item.name):
            day, why = today + timedelta(days=3), "a small errand: this week"
        else:
            day, why = _next_saturday(today), "a weekend job"
        out.append(Suggestion(item.id, item.name, day, why))
    out.sort(key=lambda s: (s.day, s.item_id))
    return out


def apply_suggestions(butler, suggestions: list[Suggestion], ids) -> int:
    wanted = {int(i) for i in ids}
    n = 0
    for s in suggestions:
        if s.item_id in wanted and butler.store.update(
                s.item_id, due=datetime.combine(s.day, datetime.min.time().replace(hour=9)).timestamp()):
            n += 1
    return n
