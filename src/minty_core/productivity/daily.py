"""The day's text: the plan (593), the wind-down (594) and the end-of-day summary (598). Pure formatting over data
the caller gathers, so it is the same whether it is asked for or pushed at a time you chose."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class DayData:
    classes: list[str] = field(default_factory=list)
    study_blocks: list[str] = field(default_factory=list)
    tasks_due: list[str] = field(default_factory=list)
    chores: list[str] = field(default_factory=list)
    habits_open: list[str] = field(default_factory=list)
    occasions: list[str] = field(default_factory=list)
    exams: list[str] = field(default_factory=list)
    # for the evening summary
    tasks_done: int = 0
    chores_done: int = 0
    habits_done: list[str] = field(default_factory=list)
    cards_reviewed: int = 0
    notes_saved: int = 0
    changes: int = 0
    first_tomorrow: str = ""


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' * (n != 1)}"


def daily_plan(day: date, d: DayData) -> str:
    rows = [("Classes", d.classes), ("Study", d.study_blocks), ("To do", d.tasks_due), ("Chores", d.chores),
            ("Habits", d.habits_open), ("Coming up", [*d.occasions, *d.exams])]
    lines = [f"{label}: {'; '.join(items)}" for label, items in rows if items]
    title = f"{day:%A} {day.day} {day:%b}"
    if not lines:
        return f"Nothing is planned for {title}. A free day."
    return f"Your plan for {title}:\n" + "\n".join(lines)


def wind_down(steps: list[str], bag: str = "", alarm: str = "") -> str:
    lines = ["Time to wind down."]
    lines += [f"{i}. {s}" for i, s in enumerate(steps, 1)]
    if bag:
        lines.append(bag)
    if alarm:
        lines.append(alarm)
    return "\n".join(lines)


def end_of_day(day: date, d: DayData) -> str:
    parts = []
    if d.tasks_done:
        parts.append(f"{_plural(d.tasks_done, 'task')} done")
    if d.chores_done:
        parts.append(f"{_plural(d.chores_done, 'chore')} ticked off")
    if d.habits_done:
        parts.append(f"{_plural(len(d.habits_done), 'habit')} ({', '.join(d.habits_done[:3])})")
    if d.cards_reviewed:
        parts.append(_plural(d.cards_reviewed, "flashcard"))
    if d.notes_saved:
        parts.append(f"{_plural(d.notes_saved, 'note')} saved")
    if not parts:
        text = "Today was quiet: nothing was ticked off."
    else:
        text = "Today: " + ", ".join(parts) + "."
        if d.changes:
            text += f" I made {_plural(d.changes, 'change')} for you."
    if d.first_tomorrow:
        text += f" Tomorrow starts with {d.first_tomorrow}."
    return text
