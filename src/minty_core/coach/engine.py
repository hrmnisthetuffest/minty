"""The training engine: plain arithmetic over the health hub's workouts. No model is involved; the model is given the
results as a facts pack and only explains them.

One primary muscle per exercise (a keyword table); a "hard set" is a working set (not a warm-up) with at least one
rep; the weekly band is 10-20 hard sets per muscle; load goes up by double progression.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

DAY = 86400.0
BAND_LOW, BAND_HIGH = 10, 20
PLATEAU_WINDOW_DAYS = 42
PLATEAU_RECENT_DAYS = 21
PLATEAU_MIN_SESSIONS = 4
RUN_JUMP_PCT = 15.0
RUN_MIN_BASE_KM = 5.0
MAX_E1RM_REPS = 12  # above this a rep-max estimate is not reliable

_MUSCLES = [(m, re.compile(rx, re.I)) for m, rx in (
    ("forearms", r"wrist|forearm|grip|farmer"),
    ("hamstrings", r"leg curl|hamstring|romanian|\brdl\b|stiff[- ]leg|good morning"),
    ("triceps", r"tricep|pushdown|push down|skull|\bdips?\b|close[- ]grip bench"),
    ("biceps", r"bicep|\bcurl|preacher|hammer"),
    ("quads", r"squat|leg press|leg extension|lunge|hack|step[- ]up|sissy"),
    ("glutes", r"hip thrust|glute|bridge|abduct"),
    ("calves", r"calf|calves"),
    ("shoulders", r"shoulder|overhead press|military|\bohp\b|lateral raise|front raise|rear delt|reverse fly|"
                  r"face pull|arnold|upright row"),
    ("chest", r"bench|chest|\bfly|flye|\bpec|push[- ]?up|crossover"),
    ("back", r"\brow\b|pull[- ]?up|chin[- ]?up|pulldown|pull down|deadlift|\blat\b|back extension|shrug|"
             r"hyperextension"),
    ("core", r"crunch|plank|sit[- ]?up|leg raise|\babs?\b|russian|ab wheel|woodchop|\bcore\b"),
)]
_COMPOUND = re.compile(r"squat|bench press|deadlift|overhead press|military|barbell row|bent over row|pull[- ]?up",
                       re.I)
_LOWER = {"quads", "hamstrings", "glutes", "calves"}


def muscle_of(exercise: str) -> str:
    for muscle, rx in _MUSCLES:
        if rx.search(exercise or ""):
            return muscle
    return "other"


@dataclass
class SetRec:
    exercise: str
    idx: int = 0
    kind: str = "normal"
    weight_kg: float | None = None
    reps: int | None = None
    rpe: float | None = None


@dataclass
class Session:
    id: int
    started: float
    title: str
    duration_s: int | None
    origin: str
    sets: list[SetRec] = field(default_factory=list)


@dataclass
class PR:
    exercise: str
    e1rm: float
    previous: float
    weight_kg: float
    reps: int


@dataclass
class Target:
    exercise: str
    weight_kg: float
    reps: int
    why: str


@dataclass
class RunLoad:
    this_week_km: float
    last_week_km: float
    change_pct: float | None
    flag: bool
    note: str = ""


def load_sessions(hub, since: float | None = None) -> list[Session]:
    """Sessions (oldest first) from the hub's workouts and sets."""
    by_workout: dict[int, list[SetRec]] = {}
    for s in hub.rows("sets"):
        by_workout.setdefault(s["workout_id"], []).append(
            SetRec(s["exercise"], s["idx"], s["kind"], s["weight_kg"], s["reps"], s["rpe"]))
    out = [Session(w["id"], w["started"], w["title"], w["duration_s"], w["origin"], by_workout.get(w["id"], []))
           for w in hub.rows("workouts") if since is None or w["started"] >= since]
    return sorted(out, key=lambda s: s.started)


# ---- formulas ------------------------------------------------------------------------------------------------
def epley(weight: float, reps: int) -> float:
    if not weight or not reps or weight <= 0 or reps <= 0:
        return 0.0
    return float(weight) if reps == 1 else float(weight) * (1 + reps / 30)


def brzycki(weight: float, reps: int) -> float:
    if not weight or not reps or weight <= 0 or reps <= 0 or reps >= 37:
        return 0.0
    return float(weight) * 36 / (37 - reps)


def is_working(s: SetRec) -> bool:
    return s.kind not in ("warmup", "warm_up", "warm-up") and (s.reps or 0) >= 1


def _best_set(sets: list[SetRec]) -> tuple[float, SetRec | None]:
    best, which = 0.0, None
    for s in sets:
        if is_working(s) and (s.reps or 0) <= MAX_E1RM_REPS:
            e = epley(s.weight_kg or 0, s.reps or 0)
            if e > best:
                best, which = e, s
    return best, which


def best_e1rm(sessions: list[Session], exercise: str, before: float | None = None) -> float:
    best = 0.0
    for sess in sessions:
        if before is not None and sess.started >= before:
            continue
        e, _ = _best_set([s for s in sess.sets if s.exercise == exercise])
        best = max(best, e)
    return best


def prs_in(session: Session, history: list[Session]) -> list[PR]:
    """Lifts where this session beat everything logged before it. A first-ever log is not a PR."""
    out = []
    for exercise in dict.fromkeys(s.exercise for s in session.sets):
        current, which = _best_set([s for s in session.sets if s.exercise == exercise])
        previous = best_e1rm(history, exercise, before=session.started)
        if which is not None and previous > 0 and current > previous * 1.0001:
            out.append(PR(exercise, current, previous, which.weight_kg or 0, which.reps or 0))
    return out


def hard_sets_by_muscle(sessions: list[Session], now: float, days: int = 7) -> dict[str, int]:
    counts: dict[str, int] = {}
    for sess in sessions:
        if now - days * DAY < sess.started <= now:
            for s in sess.sets:
                if is_working(s):
                    m = muscle_of(s.exercise)
                    counts[m] = counts.get(m, 0) + 1
    return counts


def volume_status(sets: int) -> str:
    return "low" if sets < BAND_LOW else "high" if sets > BAND_HIGH else "in range"


# ---- progression ---------------------------------------------------------------------------------------------
def rep_range(exercise: str) -> tuple[int, int]:
    return (5, 8) if _COMPOUND.search(exercise) else (8, 12)


def _increment(exercise: str) -> float:
    if re.search(r"deadlift", exercise, re.I) and muscle_of(exercise) != "hamstrings":
        return 5.0
    return 5.0 if muscle_of(exercise) in _LOWER else 2.5


def next_targets(sessions: list[Session], exercises: list[str]) -> list[Target]:
    """Double progression: add a rep to the weakest set; when every set is at the top of the range, add load and drop
    back to the bottom of it."""
    out = []
    for exercise in exercises:
        last = next((s for s in sorted(sessions, key=lambda s: -s.started)
                     if any(x.exercise == exercise and is_working(x) for x in s.sets)), None)
        if last is None:
            continue
        work = [x for x in last.sets if x.exercise == exercise and is_working(x)]
        top = max(x.weight_kg or 0 for x in work)
        at_top = [x for x in work if (x.weight_kg or 0) == top]
        lo, hi = rep_range(exercise)
        weakest = min(x.reps or 0 for x in at_top)
        if weakest >= hi:
            inc = _increment(exercise)
            out.append(Target(exercise, round(top + inc, 2), lo,
                              f"every set reached {hi} reps at {top:g} kg, so add {inc:g} kg and go back to {lo} reps"))
        else:
            out.append(Target(exercise, top, min(hi, weakest + 1),
                              f"your weakest set was {weakest} reps at {top:g} kg; add a rep (range {lo}-{hi})"))
    return out


def plateau(sessions: list[Session], now: float, exercise: str) -> bool:
    mine = [s for s in sessions if any(x.exercise == exercise and is_working(x) for x in s.sets)]
    window = [s for s in mine if now - PLATEAU_WINDOW_DAYS * DAY <= s.started <= now]
    if len(window) < PLATEAU_MIN_SESSIONS:
        return False
    cut = now - PLATEAU_RECENT_DAYS * DAY
    recent = best_e1rm([s for s in mine if s.started >= cut], exercise)
    earlier = best_e1rm([s for s in mine if s.started < cut], exercise)
    return earlier > 0 and recent <= earlier * 1.005


def stalled_lifts(sessions: list[Session], now: float, top: int = 6) -> list[str]:
    """Among the lifts you trained most in the last six weeks, the ones with no new best."""
    counts: dict[str, int] = {}
    for s in sessions:
        if now - PLATEAU_WINDOW_DAYS * DAY <= s.started <= now:
            for x in s.sets:
                if is_working(x):
                    counts[x.exercise] = counts.get(x.exercise, 0) + 1
    ranked = [e for e, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:top]]
    return [e for e in ranked if plateau(sessions, now, e)]


def adherence(sessions: list[Session], now: float, per_week: int, weeks: int = 4) -> float:
    if per_week <= 0:
        return 0.0
    n = sum(1 for s in sessions if now - weeks * 7 * DAY <= s.started <= now)
    return n / (per_week * weeks)


def running_load(runs: list[tuple[float, float]], now: float) -> RunLoad:
    """runs: (timestamp, metres). This week against last week; a jump over 15 % is flagged, if last week was a real
    base (5 km)."""
    this = sum(m for t, m in runs if now - 7 * DAY < t <= now) / 1000
    last = sum(m for t, m in runs if now - 14 * DAY < t <= now - 7 * DAY) / 1000
    if last < RUN_MIN_BASE_KM:
        return RunLoad(this, last, None, False, "Too little running last week to judge a jump.")
    change = (this - last) / last * 100
    return RunLoad(this, last, round(change, 1), change > RUN_JUMP_PCT)
