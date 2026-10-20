"""The pre-workout brief and the post-workout debrief (LF-10).

Before a session, on request or at a time you set: today's routine, targets from your last performance, a warm-up
ramp, a sleep-based hint, the usual duration, and a shorter session in exam week. After a session, when it has synced:
PRs, hard sets per muscle against the weekly band, and the next targets. Nothing is said during training: a debrief is
built only for a workout that has finished and arrived.
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Callable

from minty_core.coach import engine, nutrition, planning

DAY = 86400.0
FRESH_S = 12 * 3600  # a workout older than this when it arrives gets no debrief (the moment has passed)
PLATE = 2.5
RAMP = ((0.5, 8), (0.7, 5), (0.85, 3))
META_LAST = "last_debrief_workout_id"


@dataclass
class Brief:
    text: str
    facts: str = ""
    rules: list[str] = field(default_factory=list)
    title: str = ""


@dataclass
class Debrief:
    text: str
    workout_id: int
    prs: list = field(default_factory=list)
    facts: str = ""
    rules: list[str] = field(default_factory=list)


def next_title(titles: list[str]) -> str | None:
    """The session that follows your own rotation: whatever came after the previous time you did the latest one."""
    titles = [t for t in titles if t]
    if not titles:
        return None
    last = titles[-1]
    for j in range(len(titles) - 2, -1, -1):
        if titles[j] == last:
            return titles[j + 1]
    return last


def _plate(x: float) -> float:
    return round(x / PLATE) * PLATE


def _kg(x: float) -> str:
    return f"{x:g} kg"


class Coach:
    def __init__(self, hub, clock: Callable[[], float] = time.time,
                 planning_on: Callable[[], bool] = lambda: True):
        self.hub, self._clock, self._planning = hub, clock, planning_on

    # ---- before -----------------------------------------------------------------------------------
    def pre_workout(self, title: str | None = None, log: bool = True) -> Brief:
        sessions = engine.load_sessions(self.hub)
        if not sessions:
            return Brief("I have no workouts logged yet, so there is nothing to base a brief on.")
        title = title or next_title([s.title for s in sessions])
        last = next((s for s in reversed(sessions) if s.title == title), None)
        if last is None:
            return Brief(f"I haven't seen a session called {title} yet, so I have no numbers to base it on.",
                         title=title or "")
        exercises = list(dict.fromkeys(s.exercise for s in last.sets if engine.is_working(s)))
        targets = engine.next_targets(sessions, exercises)
        lines = [f"Next up looks like {title}, following your usual order."]
        if targets:
            lines.append("Targets (weight x reps): " + "; ".join(f"{t.exercise} {_kg(t.weight_kg)} x {t.reps}" for t in targets[:6])
                         + ".")
            first = targets[0]
            ramp = ", ".join(f"{_kg(_plate(first.weight_kg * f))} x {r}" for f, r in RAMP)
            lines.append(f"Warm-up for {first.exercise}: {ramp}.")
        same = [s.duration_s for s in sessions if s.title == title and s.duration_s][-3:]
        if same:
            lines.append(f"It usually takes about {round(statistics.median(same) / 60)} minutes, going by your last few.")
        sleep = [(r["start"], r["minutes"]) for r in self.hub.rows("sleep")]
        ready = nutrition.readiness(sleep, self._clock())
        if ready.label != "unknown":
            lines.append(f"Sleep hint: {ready.label}. {ready.note}")
        if self._planning():
            stuck = engine.stalled_lifts(sessions, self._clock())
            nights = sum(1 for r in self.hub.rows("sleep") if self._clock() - 7 * DAY < r["start"] <= self._clock()
                         and r["minutes"] < 360)
            hint = nutrition.recovery_hint(len(stuck), nights)
            if hint:
                lines.append(hint)
        if self.hub.profile().get("exam_week") == "1":
            lines.append("Exam week: keep it shorter, about 70% of the usual sets, and finish early in the evening.")
        facts = f"Last {title}: " + "; ".join(
            f"{t.exercise} next {_kg(t.weight_kg)} x {t.reps}" for t in targets[:6])
        rules = [t.why for t in targets[:3]] + (["double progression: add a rep, then add load"] if targets else [])
        if log and targets:
            self.hub.log_advice("pre-workout", " ".join(lines)[:600], facts, rules)
        return Brief(" ".join(lines), facts, rules, title)

    def scheduled_brief(self, now, hhmm: str) -> Brief | None:
        """At most one brief a day, once the set time has passed, and only if you have not trained yet today."""
        if not hhmm or now.strftime("%H:%M") < hhmm:
            return None
        today = now.strftime("%Y-%m-%d")
        if self.hub.meta_get("last_brief_day") == today:
            return None
        self.hub.meta_set("last_brief_day", today)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        if any(w["started"] >= midnight for w in self.hub.rows("workouts")):
            return None
        brief = self.pre_workout()
        return brief if brief.facts else None

    # ---- after ------------------------------------------------------------------------------------
    def _debrief(self, sessions: list[engine.Session], session: engine.Session) -> Debrief:
        earlier = [s for s in sessions if s.started < session.started]
        prs = engine.prs_in(session, earlier)
        now = self._clock()
        week = engine.hard_sets_by_muscle(sessions, now, days=7)
        today: dict[str, int] = {}
        for s in session.sets:
            if engine.is_working(s):
                m = engine.muscle_of(s.exercise)
                today[m] = today.get(m, 0) + 1
        lines = [f"Good session: {session.title}."]
        if prs:
            lines.append("PR: " + "; ".join(f"{p.exercise} estimated max {p.e1rm:.1f} kg (was {p.previous:.1f})"
                                             for p in prs[:3]) + ".")
        vol = "; ".join(f"{m} {n} hard sets today ({week.get(m, n)} this week, {engine.volume_status(week.get(m, n))})"
                        for m, n in sorted(today.items(), key=lambda kv: -kv[1])[:4] if m != "other")
        if vol:
            lines.append("Volume: " + vol + ".")
        exercises = list(dict.fromkeys(s.exercise for s in session.sets if engine.is_working(s)))
        if self._planning():
            board = planning.pr_board(sessions, now)
            mine = [m for m in board.milestones if any(m.startswith(f"{e}:") for e in exercises)]
            if mine:
                lines.append("Milestone: " + "; ".join(mine[:2]) + ".")
        targets = engine.next_targets(sessions, exercises)
        if targets:
            lines.append("Next time: " + "; ".join(f"{t.exercise} {_kg(t.weight_kg)} x {t.reps}"
                                                   for t in targets[:5]) + ".")
        facts = f"{session.title}: " + ", ".join(f"{m} {n} sets" for m, n in today.items())
        rules = [f"PR: new estimated max on {p.exercise}" for p in prs] + [t.why for t in targets[:2]]
        text = " ".join(lines)
        self.hub.log_advice("post-workout", text, facts, rules)
        return Debrief(text, session.id, prs, facts, rules)

    def pending_debriefs(self) -> list[Debrief]:
        """A debrief for each workout that has arrived since the last check and finished within the last 12 hours."""
        seen = int(self.hub.meta_get(META_LAST, "0") or 0)
        if self.hub.max_workout_id() <= seen:
            return []  # nothing new since the last look: no need to read the history
        sessions = engine.load_sessions(self.hub)
        fresh = [s for s in sessions if s.id > seen]
        if not fresh:
            return []
        self.hub.meta_set(META_LAST, str(max(s.id for s in fresh)))
        now = self._clock()
        out = []
        for s in fresh:
            end = s.started + (s.duration_s or 0)
            if now - end <= FRESH_S and engine.is_working(next((x for x in s.sets if engine.is_working(x)),
                                                                engine.SetRec("", reps=0))):
                out.append(self._debrief(sessions, s))
        return out
