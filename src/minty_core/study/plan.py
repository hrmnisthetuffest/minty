"""Study planning: homework into free evening time (576), exam countdown and revision (577), deadline pacing
backwards from the due day (943) and timetable-aware reminders (726).

Plans only use time that is free: never the night window, never your tuition or sport. What does
not fit before its deadline is said not to fit; it is never squeezed in."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ROUND = 5  # minutes


def _hm(text: str) -> int:
    h, m = str(text).split(":")
    return int(h) * 60 + int(m)


def _time(minutes: int) -> time:
    return time(minutes // 60, minutes % 60)


@dataclass(frozen=True)
class Task:
    key: str
    subject: str
    title: str
    minutes: int
    due: date


@dataclass(frozen=True)
class Block:
    day: date
    start: time
    end: time
    task: str
    subject: str
    title: str
    minutes: int


@dataclass
class Plan:
    blocks: list[Block] = field(default_factory=list)
    unplaced: list[Task] = field(default_factory=list)

    def text(self) -> str:
        lines = []
        for day in sorted({b.day for b in self.blocks}):
            parts = [f"{b.start:%H:%M}-{b.end:%H:%M} {b.subject}: {b.title}" for b in self.blocks if b.day == day]
            lines.append(f"{day:%A %d %b}: " + "; ".join(parts))
        for t in self.unplaced:
            lines.append(f"{t.subject}: {t.title} does not fit before {t.due:%A %d %b}; talk to your teacher or "
                         "move something.")
        return "\n".join(lines) or "Nothing to plan."


class Planner:
    def __init__(self, cfg):
        self.cfg = cfg

    @staticmethod
    def is_weekday(d: date) -> bool:
        return d.weekday() < 5

    def windows(self, d: date) -> list[tuple[int, int]]:
        s = self.cfg.study
        if self.is_weekday(d):
            lo, hi = _hm(s.window_start), _hm(s.window_end)
        else:
            lo, hi = _hm(s.weekend_start), _hm(s.weekend_end)
        free = [(lo, hi)] if hi > lo else []
        cuts = []
        for spec in s.busy.get(DAYS[d.weekday()], []):
            try:
                a, b = spec.split("-")
                cuts.append((_hm(a), _hm(b)))
            except ValueError:
                continue
        n_from, n_to = _hm(self.cfg.proactive.night_from), _hm(self.cfg.proactive.night_to)
        cuts += [(n_from, 24 * 60), (0, n_to)] if n_from > n_to else [(n_from, n_to)]
        out = []
        for a, b in free:
            pieces = [(a, b)]
            for ca, cb in cuts:
                nxt = []
                for x, y in pieces:
                    if cb <= x or ca >= y:
                        nxt.append((x, y))
                    else:
                        if ca > x:
                            nxt.append((x, ca))
                        if cb < y:
                            nxt.append((cb, y))
                pieces = nxt
            out += pieces
        return sorted(p for p in out if p[1] - p[0] >= 10)

    # ---- placing -----------------------------------------------------------------------------------
    def _slots(self, day: date, today: date, now: datetime | None) -> list[list[int]]:
        free = [list(w) for w in self.windows(day)]
        if now is not None and day == today:
            floor = math.ceil((now.hour * 60 + now.minute) / ROUND) * ROUND
            free = [[max(a, floor), b] for a, b in free if b > floor]
        return [w for w in free if w[1] - w[0] >= 10]

    def _chunks(self, minutes: int) -> list[int]:
        block = self.cfg.study.block_min
        n = max(1, math.ceil(minutes / block))
        base, rem = divmod(minutes, n)
        return [base + (1 if i < rem else 0) for i in range(n)]

    def _put(self, state: dict, day: date, today: date, now, minutes: int) -> tuple[int, int] | None:
        used = state.setdefault(day, {"slots": self._slots(day, today, now), "minutes": 0})
        if used["minutes"] + minutes > self.cfg.study.max_minutes_per_day:
            return None
        for slot in used["slots"]:
            if slot[1] - slot[0] >= minutes:
                start = slot[0]
                slot[0] = start + minutes + self.cfg.study.break_min
                used["minutes"] += minutes
                return start, start + minutes
        return None

    def plan(self, tasks: list[Task], today: date, now: datetime | None = None) -> Plan:
        plan, state = Plan(), {}
        for t in sorted(tasks, key=lambda t: (t.due, -t.minutes, t.key)):
            last = t.due - timedelta(days=1)
            days = [today + timedelta(days=i) for i in range((last - today).days + 1)] if last >= today else []
            left = t.minutes
            for chunk in self._chunks(t.minutes):
                for d in days:
                    got = self._put(state, d, today, now, chunk)
                    if got:
                        plan.blocks.append(Block(d, _time(got[0]), _time(got[1]), t.key, t.subject, t.title, chunk))
                        left -= chunk
                        break
            if left > 0:
                plan.unplaced.append(t)
        plan.blocks.sort(key=lambda b: (b.day, b.start))
        return plan


# ---- 943 deadline pacing ---------------------------------------------------------------------------------
def pace(planner: Planner, title: str, minutes: int, due: date, today: date, subject: str = "") -> list[Block]:
    """Chunks placed backwards from the day before the deadline, one a day first, so work is spread out."""
    chunks = planner._chunks(minutes)
    days = [due - timedelta(days=i) for i in range(1, (due - today).days + 1)]  # nearest the deadline first
    state: dict = {}
    blocks: list[Block] = []
    for round_ in range(2):  # a second chunk on a day only when every day already has one
        for d in days:
            if not chunks:
                break
            if round_ == 1 and not blocks:
                break
            got = planner._put(state, d, today, None, chunks[0])
            if got:
                size = chunks.pop(0)
                blocks.append(Block(d, _time(got[0]), _time(got[1]), title, subject, title, size))
    blocks.sort(key=lambda b: (b.day, b.start))
    return blocks


# ---- 577 exams -----------------------------------------------------------------------------------------------
def countdown(exams: list[tuple[str, date, list[str]]], today: date) -> list[str]:
    out = []
    for subject, day, _topics in sorted(exams, key=lambda e: e[1]):
        n = (day - today).days
        if n < 0:
            continue
        when = "is today" if n == 0 else "is tomorrow" if n == 1 else f"in {n} days ({day:%A %d %b})"
        out.append(f"{subject} {when}")
    return out


def revision_plan(subject: str, exam: date, topics: list[str], today: date, minutes: int
                  ) -> list[tuple[date, list[str], int]]:
    n = (exam - today).days
    if n <= 0:
        return []
    days = [today + timedelta(days=i) for i in range(n)]
    review = "quick review of everything"
    slots = n - 1
    out: list[tuple[date, list[str], int]] = []
    if slots <= 0 or not topics:
        return [(days[-1], [review], minutes)]
    for i in range(slots):
        if slots >= len(topics):
            todo = [topics[i % len(topics)]]
        else:
            todo = topics[i * len(topics) // slots:(i + 1) * len(topics) // slots] or [topics[i % len(topics)]]
        out.append((days[i], todo, minutes))
    out.append((days[-1], [review], minutes))
    return out


# ---- 726 timetable -----------------------------------------------------------------------------------------
def class_reminders(timetable: dict, now: datetime, lead_min: int = 15) -> list[str]:
    out = []
    for p in timetable.get(DAYS[now.weekday()], []):
        try:
            start = _hm(p["start"])
        except (KeyError, ValueError):
            continue
        minutes_now = now.hour * 60 + now.minute
        if 0 <= start - minutes_now <= lead_min:
            bring = str(p.get("bring") or "").strip()
            out.append(f"{p.get('subject', 'Class')} at {p['start']}." + (f" Bring: {bring}." if bring else ""))
    return out


def tomorrow_bag(timetable: dict, today: date) -> str:
    tomorrow = today + timedelta(days=1)
    periods = timetable.get(DAYS[tomorrow.weekday()], [])
    if not periods:
        return ""
    parts, pack = [], []
    for p in periods:
        bring = str(p.get("bring") or "").strip()
        parts.append(f"{p.get('subject', 'Class')}" + (f" ({bring})" if bring else ""))
        if bring and bring not in pack:
            pack.append(bring)
    tail = f"Pack: {', '.join(pack)}." if pack else "Nothing special to pack."
    return f"Tomorrow: {', '.join(parts)}. {tail}"
