"""Training planning and review: programme builder, deload, PR board, streaks, balance, active minutes, goal
feasibility, missed-session rescue, what works for you, plateau post-mortem. Arithmetic over your own logs; ranges and
assumptions are always stated. Nothing here runs during a session.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from minty_core.coach import engine

DAY = 86400.0
WEEK = 7 * DAY
GUIDELINE_MIN = 150
PLATE = 2.5
MILESTONES = (40, 60, 80, 100, 120, 140, 160, 180, 200)
PUSH = ("chest", "shoulders", "triceps")
PULL = ("back", "biceps")


def _plate(x: float) -> float:
    return round(x / PLATE) * PLATE


# ---- 775 programme builder -----------------------------------------------------------------------------------
# muscle -> [(exercise, equipment tags (any of), compound)]
POOL: dict[str, list[tuple[str, frozenset, bool]]] = {
    "chest": [("Bench Press (Barbell)", frozenset({"barbell", "gym"}), True),
              ("Dumbbell Bench Press", frozenset({"dumbbell", "gym"}), True),
              ("Machine Chest Press", frozenset({"machine", "gym"}), True),
              ("Incline Dumbbell Press", frozenset({"dumbbell", "gym"}), True),
              ("Push-up", frozenset({"bodyweight"}), True), ("Cable Fly", frozenset({"cable", "gym"}), False),
              ("Band Chest Press", frozenset({"bands"}), False)],
    "back": [("Bent Over Row (Barbell)", frozenset({"barbell", "gym"}), True),
             ("One-Arm Dumbbell Row", frozenset({"dumbbell", "gym"}), True),
             ("Lat Pulldown (Cable)", frozenset({"cable", "gym"}), True),
             ("Seated Row (Machine)", frozenset({"machine", "gym"}), True),
             ("Pull-up", frozenset({"bodyweight"}), True), ("Inverted Row (Bodyweight)", frozenset({"bodyweight"}), True),
             ("Band Row", frozenset({"bands"}), False)],
    "shoulders": [("Overhead Press (Barbell)", frozenset({"barbell", "gym"}), True),
                  ("Dumbbell Shoulder Press", frozenset({"dumbbell", "gym"}), True),
                  ("Lateral Raise (Dumbbell)", frozenset({"dumbbell", "gym"}), False),
                  ("Pike Shoulder Press (Bodyweight)", frozenset({"bodyweight"}), True),
                  ("Face Pull (Cable)", frozenset({"cable", "gym"}), False),
                  ("Band Lateral Raise", frozenset({"bands"}), False)],
    "biceps": [("Barbell Curl", frozenset({"barbell", "gym"}), False), ("Dumbbell Curl", frozenset({"dumbbell", "gym"}), False),
               ("Chin-up", frozenset({"bodyweight"}), True), ("Band Curl", frozenset({"bands"}), False)],
    "triceps": [("Triceps Pushdown (Cable)", frozenset({"cable", "gym"}), False),
                ("Dumbbell Overhead Triceps Extension", frozenset({"dumbbell", "gym"}), False),
                ("Bench Dip (Bodyweight)", frozenset({"bodyweight"}), False),
                ("Close-Grip Push-up", frozenset({"bodyweight"}), True)],
    "quads": [("Squat (Barbell)", frozenset({"barbell", "gym"}), True),
              ("Goblet Squat (Dumbbell)", frozenset({"dumbbell", "gym"}), True),
              ("Leg Press (Machine)", frozenset({"machine", "gym"}), True),
              ("Bodyweight Squat", frozenset({"bodyweight"}), True),
              ("Walking Lunge (Bodyweight)", frozenset({"bodyweight"}), True),
              ("Split Squat (Dumbbell)", frozenset({"dumbbell", "gym"}), True)],
    "hamstrings": [("Romanian Deadlift (Barbell)", frozenset({"barbell", "gym"}), True),
                   ("Romanian Deadlift (Dumbbell)", frozenset({"dumbbell", "gym"}), True),
                   ("Leg Curl (Machine)", frozenset({"machine", "gym"}), False),
                   ("Single-Leg Romanian Deadlift (Bodyweight)", frozenset({"bodyweight"}), True)],
    "glutes": [("Hip Thrust (Barbell)", frozenset({"barbell", "gym"}), True),
               ("Glute Bridge (Bodyweight)", frozenset({"bodyweight"}), False)],
    "calves": [("Standing Calf Raise (Machine)", frozenset({"machine", "gym"}), False),
               ("Calf Raise (Bodyweight)", frozenset({"bodyweight"}), False)],
    "core": [("Plank", frozenset({"bodyweight"}), False), ("Hanging Leg Raise", frozenset({"bodyweight"}), False),
             ("Cable Crunch", frozenset({"cable", "gym"}), False)],
}
# a day is a list of (muscle, how many exercises, sets each)
DAYS = {
    "Full body": [("quads", 1, 4), ("chest", 1, 4), ("back", 1, 4), ("shoulders", 1, 3), ("hamstrings", 1, 3),
                  ("core", 1, 3)],
    "Upper": [("chest", 2, 3), ("back", 2, 3), ("shoulders", 2, 3), ("biceps", 1, 3), ("triceps", 1, 3)],
    "Lower": [("quads", 2, 3), ("hamstrings", 1, 3), ("glutes", 1, 3), ("calves", 1, 3), ("core", 1, 3)],
    "Push": [("chest", 2, 3), ("shoulders", 2, 3), ("triceps", 2, 3)],
    "Pull": [("back", 3, 3), ("biceps", 2, 3), ("core", 1, 3)],
    "Legs": [("quads", 2, 3), ("hamstrings", 1, 3), ("glutes", 1, 3), ("calves", 1, 3)],
}


@dataclass
class Slot:
    exercise: str
    muscle: str
    sets: int
    reps: str


@dataclass
class ProgDay:
    title: str
    slots: list[Slot] = field(default_factory=list)


@dataclass
class Programme:
    days: list[ProgDay]
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = []
        for d in self.days:
            lines.append(f"{d.title}: " + "; ".join(f"{s.exercise} {s.sets} x {s.reps}" for s in d.slots) + ".")
        lines.append("Copy these into Hevy by hand as routines; I don't write to Hevy. Weights come from your "
                     "logs once you have done each lift.")
        return "\n".join(lines + self.notes)

    def to_csv(self) -> str:
        rows = ["day,exercise,sets,reps"] + [f'{d.title},"{s.exercise}",{s.sets},{s.reps}' for d in self.days
                                             for s in d.slots]
        return "\n".join(rows) + "\n"


def _split(days: int) -> list[tuple[str, str]]:
    if days <= 3:
        return [("Full body", c) for c in "ABC"[:max(2, days)]]
    if days == 4:
        return [("Upper", "A"), ("Lower", "A"), ("Upper", "B"), ("Lower", "B")]
    order = [("Push", "A"), ("Pull", "A"), ("Legs", "A"), ("Push", "B"), ("Pull", "B"), ("Legs", "B")]
    return order[:min(6, days)]


def build_programme(days_per_week: int, equipment: set[str]) -> Programme:
    have = {e.lower() for e in equipment}
    gym = "gym" in have
    notes = []
    out = []
    taken: dict[str, int] = {}
    for kind, letter in _split(days_per_week):
        day = ProgDay(f"{kind} {letter}")
        for muscle, count, sets in DAYS[kind]:
            options = [p for p in POOL[muscle] if gym or (p[1] & have)]
            if not options:
                note = f"No exercise for {muscle} with your equipment."
                if note not in notes:
                    notes.append(note)
                continue
            for _ in range(count):
                n = taken.get(muscle, 0)
                name, _, compound = options[n % len(options)]
                taken[muscle] = n + 1
                day.slots.append(Slot(name, muscle, sets, "5-8" if compound else "10-15"))
        out.append(day)
    return Programme(out, notes)


def weekly_sets_in_programme(prog: Programme) -> dict[str, int]:
    out: dict[str, int] = {}
    for d in prog.days:
        for s in d.slots:
            out[s.muscle] = out.get(s.muscle, 0) + s.sets
    return out


# ---- 777 deload ----------------------------------------------------------------------------------------------
@dataclass
class DeloadItem:
    exercise: str
    weight_kg: float
    sets: int


@dataclass
class Deload:
    items: list[DeloadItem]
    text: str


def deload_plan(sessions: list[engine.Session], now: float, reasons: list[str]) -> Deload:
    last: dict[str, tuple[float, int]] = {}
    for s in sessions:
        if now - WEEK < s.started <= now:
            by: dict[str, list[engine.SetRec]] = {}
            for x in s.sets:
                if engine.is_working(x):
                    by.setdefault(x.exercise, []).append(x)
            for ex, sets in by.items():
                top = max(x.weight_kg or 0 for x in sets)
                prev = last.get(ex, (0.0, 0))
                last[ex] = (max(prev[0], top), max(prev[1], len(sets)))
    items = [DeloadItem(ex, _plate(w * 0.9), max(1, math.ceil(n * 0.6))) for ex, (w, n) in last.items()]
    why = "; ".join(reasons) or "a planned lighter week"
    text = ("A lighter week (about 90% of your usual weights, about 60% of the sets, stop well short of failure): "
            + "; ".join(f"{i.exercise} {i.weight_kg:g} kg x {i.sets} sets" for i in items[:6]) + f". Because: {why}.")
    return Deload(items, text if items else f"A lighter week is suggested ({why}), but I have no recent sessions to "
                                           "base the weights on.")


# ---- 778 PR board and milestones ----------------------------------------------------------------------------
@dataclass
class BoardLift:
    exercise: str
    e1rm: float
    weight_kg: float
    reps: int
    day: str


@dataclass
class PRBoard:
    lifts: list[BoardLift]
    milestones: list[str]


def pr_board(sessions: list[engine.Session], now: float) -> PRBoard:
    best: dict[str, BoardLift] = {}
    for s in sessions:
        by: dict[str, list[engine.SetRec]] = {}
        for x in s.sets:
            by.setdefault(x.exercise, []).append(x)
        for ex, sets in by.items():
            e, which = engine._best_set(sets)
            if which is not None and (ex not in best or e > best[ex].e1rm):
                best[ex] = BoardLift(ex, e, which.weight_kg or 0, which.reps or 0,
                                     datetime.fromtimestamp(s.started, timezone.utc).strftime("%Y-%m-%d"))
    milestones = []
    for ex, lift in best.items():
        before = engine.best_e1rm(sessions, ex, before=now - 30 * DAY)
        if before <= 0:
            continue
        crossed = [m for m in MILESTONES if before < m <= lift.e1rm]
        if crossed:
            milestones.append(f"{ex}: passed {crossed[-1]} kg estimated max")
    return PRBoard(sorted(best.values(), key=lambda b: -b.e1rm)[:8], milestones)


# ---- 779 consistency --------------------------------------------------------------------------------------
@dataclass
class Consistency:
    current_streak_weeks: int
    longest_streak_weeks: int
    score_pct: int


def _week_start(ts: float) -> datetime:
    d = datetime.fromtimestamp(ts, timezone.utc)
    return (d - timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def consistency(sessions: list[engine.Session], now: float, per_week: int) -> Consistency:
    if not sessions or per_week <= 0:
        return Consistency(0, 0, 0)
    counts: dict[datetime, int] = {}
    for s in sessions:
        counts[_week_start(s.started)] = counts.get(_week_start(s.started), 0) + 1
    this = _week_start(now)
    first = min(counts)
    weeks, w = [], first
    while w <= this:
        weeks.append(counts.get(w, 0) >= per_week)
        w += timedelta(days=7)
    longest = run = 0
    for ok in weeks:
        run = run + 1 if ok else 0
        longest = max(longest, run)
    ended = weeks[:-1] + ([weeks[-1]] if weeks[-1] else [])  # an unfinished week is not a break
    current = 0
    for ok in reversed(ended):
        if not ok:
            break
        current += 1
    last28 = sum(1 for s in sessions if now - 28 * DAY <= s.started <= now)
    return Consistency(current, longest, min(100, round(100 * last28 / (4 * per_week))))


# ---- 842 balance, 858 active minutes ------------------------------------------------------------------------
@dataclass
class Balance:
    sets: dict[str, int]
    push_pull_ratio: float
    flags: list[str]


def balance_map(sessions: list[engine.Session], now: float, days: int = 28) -> Balance:
    sets = engine.hard_sets_by_muscle(sessions, now, days)
    push, pull = sum(sets.get(m, 0) for m in PUSH), sum(sets.get(m, 0) for m in PULL)
    ratio = (push / pull) if pull else (99.0 if push else 1.0)
    flags = []
    if push >= 10 and ratio > 1.5:
        flags.append(f"Pushing outweighs pulling {push} sets to {pull}: add rows or pull-ups before more pressing.")
    elif pull >= 10 and ratio < 0.67:
        flags.append(f"Pulling outweighs pushing {pull} sets to {push}: add some pressing.")
    q, h = sets.get("quads", 0), sets.get("hamstrings", 0)
    if q >= 8 and (h == 0 or q / h > 2.0):
        flags.append(f"Quads {q} sets against hamstrings {h}: add hamstring work (curls, Romanian deadlifts).")
    return Balance(sets, round(ratio, 2), flags)


@dataclass
class Minutes:
    minutes: int
    pct_of_guideline: float


def active_minutes(sessions: list[engine.Session], activities: list[tuple[float, float]], now: float,
                   days: int = 7) -> Minutes:
    secs = sum((s.duration_s or 0) for s in sessions if now - days * DAY < s.started <= now)
    secs += sum(d for t, d in activities if now - days * DAY < t <= now)
    minutes = round(secs / 60)
    return Minutes(minutes, round(100 * minutes / GUIDELINE_MIN, 1))


# ---- 977 reverse planner --------------------------------------------------------------------------------------
@dataclass
class GoalPlan:
    verdict: str  # realistic | stretch | not realistic | unknown
    required_per_week: float | None
    observed_per_week: float | None
    text: str


def goal_plan(sessions: list[engine.Session], now: float, exercise: str, target_e1rm: float,
              weeks_left: float) -> GoalPlan:
    pts = []
    for s in sessions:
        if now - 12 * WEEK <= s.started <= now:
            e, _ = engine._best_set([x for x in s.sets if x.exercise == exercise])
            if e:
                pts.append((s.started, e))
    pts.sort()
    span = (pts[-1][0] - pts[0][0]) / WEEK if len(pts) >= 2 else 0
    if len(pts) < 4 or span < 3:
        return GoalPlan("unknown", None, None,
                        f"I don't have enough data on {exercise} to forecast: I need at least four sessions over "
                        f"three weeks and have {len(pts)} over {span:.0f}.")
    xs = [(t - pts[0][0]) / WEEK for t, _ in pts]
    ys = [e for _, e in pts]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    current = pts[-1][1]
    need = max(0.0, (target_e1rm - current) / max(weeks_left, 0.1))
    low, high = current + slope * weeks_left * 0.5, current + slope * weeks_left * 1.5
    if need <= max(slope, 0.0) or target_e1rm <= current:
        verdict = "realistic"
    elif need <= 2 * max(slope, 0.0):
        verdict = "stretch"
    else:
        verdict = "not realistic"
    text = (f"{exercise}: your estimated max is {current:.1f} kg and has been rising about {slope:.1f} kg a week. "
            f"To reach {target_e1rm:g} kg in {weeks_left:g} weeks you need about {need:.1f} kg a week, so this is "
            f"{verdict}{' in that time' if verdict == 'not realistic' else ''}. On your recent pace the range at the "
            f"end is {low:.0f}-{high:.0f} kg. Assumptions: your recent trend continues; sleep, food and illness "
            "can move it.")
    return GoalPlan(verdict, round(need, 2), round(slope, 2), text)


# ---- 979 missed-session rescue --------------------------------------------------------------------------------
@dataclass
class Rescue:
    day: int | None
    text: str


def rescue_plan(week: list[tuple[int, str, set], ...], missed_index: int, today: int) -> Rescue:
    """week: (day 0-6, title, muscles). Move the missed session to a later day that is at least two days from every
    session sharing a muscle with it (never two heavy days on one muscle inside 48 hours); else skip it."""
    _, title, muscles = week[missed_index]
    others = [w for i, w in enumerate(week) if i != missed_index]
    shared = [o for o in others if o[2] & muscles]
    used = {o[0] for o in others}
    candidates = [d for d in range(max(0, today), 7) if all(abs(o[0] - d) >= 2 for o in shared)]
    free = [d for d in candidates if d not in used]
    for d in free + [d for d in candidates if d in used]:
        return Rescue(d, f"Move {title} to day {d + 1} of this week: it keeps 48 hours from any other session on "
                         "the same muscles.")
    return Rescue(None, f"Skip {title} this week and don't double up: every remaining day is within 48 hours of "
                        "another session on the same muscles.")


# ---- 981 what works for you, 983 plateau post-mortem ----------------------------------------------------------
def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or statistics.pstdev(xs) == 0 or statistics.pstdev(ys) == 0:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return num / math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))


def what_works(weekly: list[dict]) -> str:
    """weekly: [{"sessions", "e1rm_change", "sleep_h" | None, "protein_g" | None}] oldest first."""
    rows = [w for w in weekly if w.get("e1rm_change") is not None]
    if len(rows) < 8:
        return (f"I need at least 8 weeks with training and strength numbers to look for patterns; I have "
                f"{len(rows)}.")
    lines = []
    for key, label in (("sessions", "sessions per week"), ("sleep_h", "sleep"), ("protein_g", "protein")):
        pairs = [(w[key], w["e1rm_change"]) for w in rows if w.get(key) is not None]
        if len(pairs) < 8:
            lines.append(f"Not enough weeks with {label} logged to say ({len(pairs)}).")
            continue
        r = _pearson([p[0] for p in pairs], [p[1] for p in pairs])
        if r is None or abs(r) < 0.4:
            lines.append(f"No clear link between {label} and your strength change over {len(pairs)} weeks.")
        else:
            lines.append(f"Weeks with {'more' if r > 0 else 'less'} {label} tended to go with bigger strength gains "
                         f"(over {len(pairs)} weeks).")
    return " ".join(lines) + " These are patterns in your own numbers, not proof of cause."
