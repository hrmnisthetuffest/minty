"""One place to see training, recovery and money, a 60-second briefing, an honest weekly review, goals with
key results, and a two-tap daily check-in.

Everything is read from the coach hub and the money ledger; nothing is estimated that was not logged. Three views only.
The weekly review compares this week with the one before and with your own targets, and every number in its text is
tracked so a test can check none was invented. Body weight never appears.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from minty_core.coach import engine, nutrition
from minty_core.finance import money
from minty_core.finance_insights import budget_status, due_bills

DAY = 86400.0
VIEWS = ("training", "recovery", "money")
ENERGY = ("low", "ok", "high")
PROTEIN_LOW_FACTOR = 1.4


class _Facts(set):
    """Numbers that appear in a text, collected as they are formatted."""

    def n(self, value, spec: str = "g") -> str:
        text = format(value, spec)
        self.update(t for t in _digits(text))
        return text

    def s(self, text: str) -> str:
        self.update(_digits(text))
        return text


def _digits(text: str) -> list[str]:
    import re

    return re.findall(r"\d+(?:\.\d+)?", text)


def _weight(hub) -> float:
    rows = sorted((r["ts"], r["value"]) for r in hub.rows("body") if r["kind"] == "weight"
                  and r["residency"] != "local_only")
    return rows[-1][1] if rows else 0.0


def _nights(hub, start: float, end: float) -> list[float]:
    return [r["minutes"] / 60 for r in hub.rows("sleep") if start < r["start"] <= end]


def _protein(hub, start: float, end: float) -> list[float]:
    a, b = datetime.fromtimestamp(start).strftime("%Y-%m-%d"), datetime.fromtimestamp(end).strftime("%Y-%m-%d")
    return [r["protein_g"] for r in hub.rows("nutrition") if a < r["day"] <= b and r["protein_g"] is not None
            and r["residency"] != "local_only"]


# ---- views -----------------------------------------------------------------------------------------------
def dashboard(view: str, hub, ledger, now: float, today: date) -> str:
    if view not in VIEWS:
        raise ValueError(f"views are {', '.join(VIEWS)}")
    if view == "training":
        sessions = engine.load_sessions(hub)
        this = [s for s in sessions if now - 7 * DAY < s.started <= now]
        last = [s for s in sessions if now - 14 * DAY < s.started <= now - 7 * DAY]
        lines = [f"{len(this)} session{'s' * (len(this) != 1)} this week ({len(last)} last week)."]
        sets = engine.hard_sets_by_muscle(sessions, now, 7)
        shown = sorted(((m, n) for m, n in sets.items() if m != "other"), key=lambda kv: -kv[1])[:4]
        if shown:
            lines.append("Hard sets: " + ", ".join(f"{m} {n}" for m, n in shown) + ".")
        prs = [p for s in this for p in engine.prs_in(s, [x for x in sessions if x.started < s.started])]
        if prs:
            lines.append(f"{len(prs)} new estimated max{'es' if len(prs) != 1 else ''} this week.")
        return "\n".join(lines)
    if view == "recovery":
        nights = _nights(hub, now - 7 * DAY, now)
        lines = []
        if nights:
            lo, hi = nutrition.sleep_target(hub.age_band())
            lines.append(f"Sleep {statistics.mean(nights):.1f} h a night over {len(nights)} nights "
                         f"(range {lo:g}-{hi:g} h).")
        prot = _protein(hub, now - 7 * DAY, now)
        band = nutrition.protein_band(_weight(hub))
        if prot:
            lines.append(f"Protein {statistics.mean(prot):.0f} g a day on {len(prot)} logged days"
                         + (f" (band {band[0]}-{band[1]} g)." if band else "."))
        return "\n".join(lines) or "Nothing logged for sleep or food this week."
    month = today.strftime("%Y-%m")
    lines = []
    for b in budget_status(ledger, month, today)[:5]:
        lines.append(f"{b.category}: {b.pct}% of {money(b.limit_minor)} ({b.status}).")
    due = due_bills(ledger, today, 7)
    if due:
        lines.append(f"{len(due)} bill{'s' * (len(due) != 1)} due in the next week.")
    return "\n".join(lines) or "No budgets or bills set yet."


def briefing(hub, ledger, now: float, today: date) -> str:
    parts = []
    sessions = engine.load_sessions(hub)
    this = [s for s in sessions if now - 7 * DAY < s.started <= now]
    per = hub.profile().get("sessions_per_week", "")
    if this or per.isdigit():
        parts.append(f"Training: {len(this)} session{'s' * (len(this) != 1)} this week"
                     + (f" of {per}." if per.isdigit() else "."))
    nights = _nights(hub, now - 3 * DAY, now)
    if nights:
        parts.append(f"Sleep has averaged {statistics.mean(nights):.1f} h over the last {len(nights)} nights.")
    prot = _protein(hub, now - 3 * DAY, now)
    if prot:
        parts.append(f"Protein has averaged {statistics.mean(prot):.0f} g.")
    over = [b for b in budget_status(ledger, today.strftime("%Y-%m"), today) if b.status != "on track"]
    if over:
        parts.append("Money: " + "; ".join(f"{b.category} {b.status}" for b in over[:3]) + ".")
    due = due_bills(ledger, today, 3)
    if due:
        parts.append(f"{len(due)} bill{'s' * (len(due) != 1)} due within three days.")
    goals = okr_progress(hub, now, today)
    behind = [g for g in goals if g["pace"] == "behind"]
    if behind:
        parts.append(f"Goal behind pace: {behind[0]['key_result']} ({behind[0]['pct']}%).")
    if not parts:
        return "I have nothing logged to brief you on yet."
    return " ".join(parts)


# ---- weekly review -----------------------------------------------------------------------------------------
@dataclass
class Review:
    wins: list[str] = field(default_factory=list)
    misses: list[str] = field(default_factory=list)
    suggestion: str = ""
    text: str = ""
    facts: set = field(default_factory=set)


def weekly_review(hub, ledger, now: float, today: date) -> Review:
    f = _Facts()
    wins, misses = [], []
    sessions = engine.load_sessions(hub)
    this = [s for s in sessions if now - 7 * DAY < s.started <= now]
    suggestion_pool: list[tuple[int, str]] = []
    per = hub.profile().get("sessions_per_week", "")
    month_spend = any(b.spent_minor for b in budget_status(ledger, today.strftime("%Y-%m"), today))
    if not (this or _nights(hub, now - 7 * DAY, now) or _protein(hub, now - 7 * DAY, now) or month_spend
            or hub.checkins((today - timedelta(days=6)).isoformat(), today.isoformat())):
        return Review([], [], "", "Not enough is logged this week to review. Log sessions, sleep or food (or set a "
                                  "budget) and I'll have something real to say.", set())
    # strength
    for s in this:
        for p in engine.prs_in(s, [x for x in sessions if x.started < s.started]):
            wins.append(f"New estimated max: {p.exercise} {f.n(round(p.e1rm, 1), '.1f')} kg "
                        f"(was {f.n(round(p.previous, 1), '.1f')}).")
    # sessions against target
    if per.isdigit():
        n, target = len(this), int(per)
        if n >= target:
            wins.append(f"You trained {f.n(n)} of {f.n(target)} planned sessions.")
        else:
            misses.append(f"Sessions: {f.n(n)} of {f.n(target)} planned.")
            suggestion_pool.append((3, f"You did {f.n(n)} of {f.n(target)} planned sessions; put the missing one in "
                                       "the diary for tomorrow now, before the week gets away."))
    # sleep
    nights, prev_nights = _nights(hub, now - 7 * DAY, now), _nights(hub, now - 14 * DAY, now - 7 * DAY)
    if nights:
        avg = statistics.mean(nights)
        lo = nutrition.sleep_target(hub.age_band())[0]
        if prev_nights and avg - statistics.mean(prev_nights) >= 0.3:
            wins.append(f"Sleep averaged {f.n(round(avg, 1), '.1f')} h, up "
                        f"{f.n(round(avg - statistics.mean(prev_nights), 1), '.1f')} h on last week.")
        short = sum(1 for h in nights if h < 6)
        if short >= 3 or avg < lo:
            misses.append(f"Sleep averaged {f.n(round(avg, 1), '.1f')} h with {f.n(short)} short night"
                          f"{'s' * (short != 1)}.")
            suggestion_pool.append((1, f"Sleep is the lever: aim for {f.n(lo)} h on the nights you control and keep "
                                       "bedtime steady."))
    # protein
    prot = _protein(hub, now - 7 * DAY, now)
    band = nutrition.protein_band(_weight(hub))
    if prot and band:
        in_band = sum(1 for p in prot if p >= band[0])
        if in_band == len(prot):
            wins.append(f"Protein was in your usual range ({f.n(band[0])}-{f.n(band[1])} g) on {f.n(in_band)} of "
                        f"{f.n(len(prot))} logged days.")
        elif in_band <= len(prot) / 2:
            misses.append(f"Protein reached the low end of your range on {f.n(in_band)} of {f.n(len(prot))} logged "
                          "days.")
            suggestion_pool.append((2, "Add one protein food at your usual meals (a bowl of dal, curd or an egg) "
                                       "rather than changing everything."))
    # money
    for b in budget_status(ledger, today.strftime("%Y-%m"), today):
        if b.status == "over":
            misses.append(f"{b.category} is over its budget for the month ({f.s(money(b.spent_minor))} of "
                          f"{f.s(money(b.limit_minor))}).")
            suggestion_pool.append((4, f"Pause {b.category} spending until the month turns, or move the budget on "
                                       "purpose."))
    # check-ins
    c = checkin_summary(hub, (today - timedelta(days=6)).isoformat(), today.isoformat())
    if hub.checkins("0000-01-01", "9999-12-31"):
        (wins if c.days >= 5 else misses).append(f"You checked in on {f.n(c.days)} of {f.n(7)} days.")
    # goals
    for g in okr_progress(hub, now, today):
        if g["pace"] == "behind" and g["weeks_left"] > 0:
            misses.append(f"Goal behind pace: {g['key_result']} at {f.n(g['pct'])}% with {f.n(g['weeks_left'])} "
                          "weeks left.")
    suggestion = sorted(suggestion_pool)[0][1] if suggestion_pool else (
        "Nothing needs fixing from these numbers; keep the week as it is." if (wins or misses) else "")
    if not (wins or misses):
        text = "Nothing stands out this week, good or bad, from what is logged."
    else:
        parts = []
        if wins:
            parts.append("Wins: " + " ".join(wins))
        if misses:
            parts.append("Misses: " + " ".join(misses))
        parts.append("One suggestion: " + suggestion)
        text = " ".join(parts)
        f.update(_digits(text))  # numbers the pieces carried (formatted through f.n above)
    return Review(wins, misses, suggestion, text, set(f))


# ---- goals -----------------------------------------------------------------------------------------------
def add_okr(hub, objective: str, key_result: str, target: float, *, unit: str = "", due: str,
            source: str = "manual") -> int:
    return hub.add_okr_row(objective, key_result, unit, target, due, source)


def _current(hub, row: dict, now: float) -> float:
    src = row["source"]
    if src.startswith("e1rm:"):
        return engine.best_e1rm(engine.load_sessions(hub), src[5:])
    if src == "sessions_week":
        return float(sum(1 for w in hub.rows("workouts") if now - 7 * DAY < w["started"] <= now))
    if src == "protein_avg":
        p = _protein(hub, now - 7 * DAY, now)
        return statistics.mean(p) if p else 0.0
    return float(row["current"])


def okr_progress(hub, now: float, today: date) -> list[dict]:
    out = []
    for r in hub.okrs():
        cur = _current(hub, r, now)
        pct = round(cur / r["target"] * 100)
        due = datetime.strptime(r["due"], "%Y-%m-%d").date()
        weeks_left = max(0, (due - today).days // 7)
        span = max(1.0, datetime.strptime(r["due"], "%Y-%m-%d").timestamp() - r["created"])
        elapsed = min(1.0, max(0.0, (now - r["created"]) / span))
        expected = elapsed * 100
        pace = "ahead" if pct >= expected * 1.1 + 5 else "on pace" if pct >= expected * 0.9 - 5 else "behind"
        out.append({**r, "current": round(cur, 2), "pct": pct, "weeks_left": weeks_left, "pace": pace})
    return out


# ---- what is due now ---------------------------------------------------------------------------------------
def due_events(hub, ledger, cfg: dict, now: datetime) -> list[dict]:
    """The check-in (once a day after its time) and the weekly review (Sundays, once, after its time)."""
    out = []
    hhmm = now.strftime("%H:%M")
    today = now.strftime("%Y-%m-%d")
    if cfg.get("checkins") and hhmm >= str(cfg.get("checkin_time") or "20:00") and \
            hub.meta_get("last_checkin_day") != today:
        hub.meta_set("last_checkin_day", today)
        out.append({"type": "life", "kind": "checkin", "day": today})
    week = now.strftime("%G-W%V")
    if cfg.get("review_push") and now.weekday() == 6 and hhmm >= str(cfg.get("review_time") or "18:00") and \
            hub.meta_get("last_review_week") != week:
        hub.meta_set("last_review_week", week)
        review = weekly_review(hub, ledger, now.timestamp(), now.date())
        out.append({"type": "life", "kind": "review", "text": review.text})
    return out


# ---- check-ins -----------------------------------------------------------------------------------------------
def save_checkin(hub, day: str, energy: str | None = None, priority_done: bool | None = None) -> None:
    if energy is not None and energy not in ENERGY:
        raise ValueError(f"energy is {', '.join(ENERGY)}")
    datetime.strptime(day, "%Y-%m-%d")
    hub.save_checkin_row(day, energy, priority_done)


@dataclass
class CheckinSummary:
    days: int
    energy: dict
    priority_done: int
    priority_asked: int


def checkin_summary(hub, start_day: str, end_day: str) -> CheckinSummary:
    rows = [r for r in hub.checkins(start_day, end_day) if r["energy"] is not None or r["priority_done"] is not None]
    return CheckinSummary(len(rows), dict(Counter(r["energy"] for r in rows if r["energy"])),
                          sum(1 for r in rows if r["priority_done"] == 1),
                          sum(1 for r in rows if r["priority_done"] is not None))
