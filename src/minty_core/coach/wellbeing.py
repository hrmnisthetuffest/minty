"""Food and recovery views built from your own logs: macros left today, a morning note, a weekly report, bedtime
consistency, sleep banking, load against recovery, your own normal ranges, and a progress view that never mentions the
scale. Body-neutral by construction: protein, fuel, water, sleep, load and consistency only. Every figure says how many
days or nights it stands on."""
from __future__ import annotations

import statistics
from datetime import datetime

from minty_core.coach import engine, nutrition, planning

DAY = 86400.0
MIN_RANGE_N = 10
Z_FLAG = 1.5


def _weight(hub) -> float:
    rows = sorted((r["ts"], r["value"]) for r in hub.rows("body") if r["kind"] == "weight"
                  and r["residency"] != "local_only")
    return rows[-1][1] if rows else 0.0


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def macros_left(hub, day: str, maintenance_kcal: float | None) -> str:
    rows = [r for r in hub.rows("nutrition") if r["day"] == day and r["residency"] != "local_only"]
    if not rows:
        return f"Nothing logged for {day} yet, so I can't say what's left."
    kcal = sum(r["kcal"] or 0 for r in rows)
    protein = sum(r["protein_g"] or 0 for r in rows)
    band = nutrition.protein_band(_weight(hub))
    parts = []
    if band:
        if protein < band[0]:
            parts.append(f"Protein: {protein:g} g so far; {band[0] - protein:g} g more reaches the low end of your "
                         f"usual band ({band[0]}-{band[1]} g).")
        else:
            parts.append(f"Protein: {protein:g} g, inside or above your usual band ({band[0]}-{band[1]} g).")
    else:
        parts.append(f"Protein: {protein:g} g so far (I need a body weight in your log to give a band).")
    if maintenance_kcal:
        left = maintenance_kcal - kcal
        parts.append(f"Fuel: about {left:g} kcal more gets you to your usual {maintenance_kcal:g} kcal."
                     if left > 0 else f"Fuel: {kcal:g} kcal logged, which reaches your usual {maintenance_kcal:g}.")
    return " ".join(parts)


def morning_note(hub, now: float) -> str:
    sleep = sorted((r["start"], r["minutes"]) for r in hub.rows("sleep"))
    if not sleep:
        return "No sleep logged, so no morning hint."
    ready = nutrition.readiness(sleep, now)
    parts = [f"Last night {sleep[-1][1] / 60:.1f} h of sleep. Hint: {ready.label}. {ready.note}"]
    yesterday = _day(now - DAY)
    food = [r for r in hub.rows("nutrition") if r["day"] == yesterday and r["protein_g"] is not None]
    if food:
        parts.append(f"{sum(r['protein_g'] for r in food):g} g protein yesterday.")
    recent = [w for w in hub.rows("workouts") if now - 3 * DAY <= w["started"] <= now]
    if recent:
        parts.append(f"{len(recent)} session{'s' * (len(recent) != 1)} in the last three days.")
    return " ".join(parts)


def weekly_report(hub, now: float) -> str:
    floor = _day(now - 6 * DAY)
    food = [r for r in hub.rows("nutrition") if r["day"] >= floor and r["residency"] != "local_only"]
    days = {r["day"] for r in food}
    parts = []
    prot = [r["protein_g"] for r in food if r["protein_g"] is not None]
    band = nutrition.protein_band(_weight(hub))
    if prot:
        avg = statistics.mean(prot)
        parts.append(f"Food was logged on {len(days)} of 7 days; protein averaged {avg:.0f} g"
                     + (f" ({nutrition.protein_status(avg, band)}; usual band {band[0]}-{band[1]} g)." if band else "."))
    else:
        parts.append("No food logged this week, so nothing to report on protein.")
    nights = [r["minutes"] for r in hub.rows("sleep") if now - 7 * DAY < r["start"] <= now]
    if nights:
        target = nutrition.sleep_target(hub.age_band())
        avg_h = statistics.mean(nights) / 60
        parts.append(f"Sleep: {avg_h:.1f} h a night over {len(nights)} nights "
                     f"({nutrition.sleep_status(avg_h, target)}; usual range {target[0]:g}-{target[1]:g} h).")
    water = [r["water_ml"] for r in food if r["water_ml"]]
    if water:
        parts.append(nutrition.hydration_hint(statistics.mean(water), _weight(hub)))
    starts = [r["start"] for r in hub.rows("sleep") if now - 14 * DAY < r["start"] <= now]
    bt = bedtime_consistency(starts)
    if bt.score is not None:
        parts.append(f"Bedtime consistency {bt.score}/100 (spread about {bt.spread_min:.0f} minutes over {bt.n} nights).")
    sessions = [w for w in hub.rows("workouts") if now - 7 * DAY < w["started"] <= now]
    parts.append(f"Training: {len(sessions)} session{'s' * (len(sessions) != 1)}.")
    return " ".join(parts)


class Bedtime:
    def __init__(self, score, spread_min, n):
        self.score, self.spread_min, self.n = score, spread_min, n


def bedtime_consistency(starts: list[float]) -> Bedtime:
    if len(starts) < 7:
        return Bedtime(None, 0.0, len(starts))
    minutes = []
    for t in starts:
        d = datetime.fromtimestamp(t)
        minutes.append((d.hour * 60 + d.minute - 720) % 1440)  # minutes after noon: no wrap at midnight
    spread = statistics.pstdev(minutes)
    return Bedtime(max(0, round(100 - spread / 120 * 100)), spread, len(starts))


def sleep_banking(avg_hours: float, upper_hours: float, days_until: int) -> str:
    if days_until < 3:
        return ("That's too close to bank much sleep. Protect the next few nights instead: the same bedtime, "
                "screens off earlier.")
    room = upper_hours - avg_hours
    if room < 0.5:
        return (f"You're already sleeping {avg_hours:.1f} h, near the top of your range, so there's little to bank. "
                "Keep it steady.")
    extra = int(min(60, round(room * 30)))
    nights = min(days_until, 5)
    return (f"For the {nights} nights before it, go to bed about {extra} minutes earlier than usual ({avg_hours:.1f} h "
            f"now, up to {upper_hours:g} h). Modest, but it helps more than one long night after.")


def stress_load(hub, now: float) -> str:
    wk = sum((w["duration_s"] or 0) for w in hub.rows("workouts") if now - 7 * DAY < w["started"] <= now) / 60
    prev = sum((w["duration_s"] or 0) for w in hub.rows("workouts") if now - 35 * DAY < w["started"] <= now - 7 * DAY)
    usual = prev / 60 / 4
    parts = []
    high = usual > 0 and wk > 1.3 * usual
    if usual > 0:
        parts.append(f"Training: {wk:.0f} min this week against your usual {usual:.0f} "
                     f"({'high' if high else 'low' if wk < 0.7 * usual else 'normal'}).")
    else:
        parts.append(f"Training: {wk:.0f} min this week (not enough earlier weeks to compare).")
    nights = [r["minutes"] for r in hub.rows("sleep") if now - 7 * DAY < r["start"] <= now]
    short = False
    if nights:
        avg = statistics.mean(nights) / 60
        lo = nutrition.sleep_target(hub.age_band())[0]
        short = avg < lo
        parts.append(f"Sleep: {avg:.1f} h a night ({'short' if short else 'fine'}).")
    exam = hub.profile().get("exam_week") == "1"
    if exam:
        parts.append("Exam week is on.")
    fb = [hub.feedback(w["id"]) for w in hub.rows("workouts") if now - 7 * DAY < w["started"] <= now]
    hard = sum(1 for f in fb if f.get("effort") == "hard" or f.get("soreness") == "lots")
    if hard:
        parts.append(f"{hard} session{'s' * (hard != 1)} marked hard or very sore.")
    if (high and (short or exam)) or (short and exam):
        parts.append("Taken together, this week is worth easing: fewer hard sets, more sleep.")
    return " ".join(parts)


def normal_ranges(hub, now: float) -> str:
    nights = sorted((r["start"], r["minutes"] / 60) for r in hub.rows("sleep") if now - 28 * DAY < r["start"] <= now)
    if len(nights) < MIN_RANGE_N + 1:
        return (f"Not enough data for normal ranges yet: I have {len(nights)} night{'s' * (len(nights) != 1)} in "
                f"the last 28 days and need at least {MIN_RANGE_N + 1}.")
    base = [h for _, h in nights[:-1]]
    last = nights[-1][1]
    mean, sd = statistics.mean(base), max(statistics.stdev(base), 0.25)
    z = (last - mean) / sd
    where = "below" if z <= -Z_FLAG else "above" if z >= Z_FLAG else "within"
    return (f"Sleep: your usual is {mean:.1f} h ± {sd:.1f} over {len(base)} nights (28 days); last night "
            f"{last:.1f} h is {where} that.")


def body_neutral_progress(hub, now: float) -> str:
    sessions = engine.load_sessions(hub)
    recent = [s for s in sessions if now - 28 * DAY < s.started <= now]
    before = [s for s in sessions if now - 56 * DAY < s.started <= now - 28 * DAY]
    prs = [(s, p) for s in recent for p in engine.prs_in(s, [x for x in sessions if x.started < s.started])]
    lifts = {p.exercise for _, p in prs}
    parts = [f"Last 28 days: {len(recent)} sessions ({len(before)} in the 28 before)."]
    if prs:
        parts.append(f"{len(prs)} new estimated max{'es' if len(prs) != 1 else ''} across {len(lifts)} "
                     f"lift{'s' * (len(lifts) != 1)}.")
    per = hub.profile().get("sessions_per_week", "")
    if per.isdigit():
        c = planning.consistency(sessions, now, int(per))
        parts.append(f"Consistency {c.score_pct}% of your target; {c.current_streak_weeks} week streak.")
    fb = [hub.feedback(w["id"]) for w in hub.rows("workouts") if now - 28 * DAY < w["started"] <= now]
    energy = [f["energy"] for f in fb if "energy" in f]
    if energy:
        parts.append(f"Energy after sessions: {energy.count('high')} high, {energy.count('ok')} ok, "
                     f"{energy.count('low')} low.")
    return " ".join(parts)
