"""Review built from the hub: the plateau post-mortem (measured facts and gaps only), the weekly series behind
"what works for you", and the return-to-training ramp after a cleared injury."""
from __future__ import annotations

import statistics
from datetime import datetime

from minty_core.coach import engine, nutrition

DAY = 86400.0
WEEK = 7 * DAY
WINDOW_DAYS = 42
RAMP = (0.5, 0.7, 0.85, 1.0)


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def plateau_postmortem(hub, exercise: str, now: float) -> str:
    sessions = engine.load_sessions(hub)
    since, before = now - WINDOW_DAYS * DAY, now - 2 * WINDOW_DAYS * DAY
    mine = [s for s in sessions if any(x.exercise == exercise and engine.is_working(x) for x in s.sets)]
    recent = [s for s in mine if since <= s.started <= now]
    best = engine.best_e1rm(mine, exercise)
    best_day = next((s for s in reversed(mine) if engine._best_set([x for x in s.sets if x.exercise == exercise])[0]
                     >= best - 1e-9), None)
    parts = [f"{exercise}: no new best estimated max in six weeks"
             + (f" (best {best:.1f} kg on {_day(best_day.started)})." if best_day else ".")]
    gaps = []
    parts.append(f"Measured over the last {WINDOW_DAYS} days: {len(recent)} session{'s' * (len(recent) != 1)} of it "
                 f"({len(recent) / 6:.1f} a week).")
    muscle = engine.muscle_of(exercise)
    now_sets = engine.hard_sets_by_muscle(sessions, now, WINDOW_DAYS).get(muscle, 0)
    old_sets = engine.hard_sets_by_muscle(sessions, now - WINDOW_DAYS * DAY, WINDOW_DAYS).get(muscle, 0)
    parts.append(f"{muscle.capitalize()}: {now_sets / 6:.1f} hard sets a week against {old_sets / 6:.1f} in the six "
                 "weeks before.")
    target = hub.profile().get("sessions_per_week", "")
    if target.isdigit():
        all_recent = [s for s in sessions if since <= s.started <= now]
        parts.append(f"All training: {len(all_recent)} sessions against a target of {int(target) * 6}.")
    else:
        gaps.append("no weekly session target is set")
    nights = [r for r in hub.rows("sleep") if since <= r["start"] <= now]
    if nights:
        parts.append(f"Sleep averaged {statistics.mean(r['minutes'] for r in nights) / 60:.1f} h on the "
                     f"{len(nights)} nights logged.")
        if len(nights) < 30:
            gaps.append(f"sleep was logged on {len(nights)} of the last {WINDOW_DAYS} days")
    else:
        gaps.append("no sleep was logged in that period")
    floor = datetime.fromtimestamp(since).strftime("%Y-%m-%d")
    food = [r for r in hub.rows("nutrition") if r["day"] >= floor and r["protein_g"] is not None]
    weights = sorted((r["ts"], r["value"]) for r in hub.rows("body") if r["kind"] == "weight"
                     and r["residency"] != "local_only")
    latest = weights[-1][1] if weights else 0
    if food:
        avg = statistics.mean(r["protein_g"] for r in food)
        band = nutrition.protein_band(latest)
        parts.append(f"Protein averaged {avg:.0f} g on the {len(food)} days logged"
                     + (f" (the usual band for {latest:g} kg is {band[0]}-{band[1]} g)." if band else "."))
        if len(food) < 30:
            gaps.append(f"food was logged on {len(food)} of the last {WINDOW_DAYS} days")
    else:
        gaps.append("no food was logged in that period")
    window_w = [w for t, w in weights if since <= t <= now]
    if len(window_w) >= 2:
        parts.append(f"Weight: {window_w[0]:g} kg at the start of the period, {window_w[-1]:g} kg at the end.")
    else:
        gaps.append("fewer than two weight entries in that period")
    parts.append("Data gaps: " + "; ".join(gaps) + "." if gaps else "No data gaps in these sources.")
    return " ".join(parts)


def weekly_series(hub, now: float, weeks: int = 26) -> list[dict]:
    sessions = engine.load_sessions(hub)
    sleep = hub.rows("sleep")
    food = hub.rows("nutrition")
    out, prev_best = [], {}
    for w in range(weeks):
        end = now - (weeks - 1 - w) * WEEK
        start = end - WEEK
        inside = [s for s in sessions if start < s.started <= end]
        best = {}
        for s in inside:
            for ex in {x.exercise for x in s.sets}:
                e, _ = engine._best_set([x for x in s.sets if x.exercise == ex])
                if e:
                    best[ex] = max(best.get(ex, 0.0), e)
        changes = [100 * (b - prev_best[ex]) / prev_best[ex] for ex, b in best.items() if prev_best.get(ex)]
        prev_best.update(best)
        nights = [r["minutes"] for r in sleep if start < r["start"] <= end]
        days = (datetime.fromtimestamp(start).strftime("%Y-%m-%d"), datetime.fromtimestamp(end).strftime("%Y-%m-%d"))
        prot = [r["protein_g"] for r in food if days[0] < r["day"] <= days[1] and r["protein_g"] is not None]
        out.append({"sessions": len(inside), "e1rm_change": statistics.mean(changes) if changes else None,
                    "sleep_h": statistics.mean(nights) / 60 if nights else None,
                    "protein_g": statistics.mean(prot) if prot else None})
    return out


AREA_MUSCLES = {"knee": "legs", "ankle": "calves and legs", "hip": "glutes and legs", "back": "back and hamstrings",
                "shoulder": "shoulders, chest and back", "elbow": "arms", "wrist": "arms and presses",
                "neck": "upper back and shoulders"}


def injury_ramp(hub, injury_id: int, now: float) -> str:
    row = next((i for i in hub.injuries() if i["id"] == injury_id), None)
    if row is None:
        return "I have no injury with that number."
    if row["cleared_at"] is None:
        return (f"Your {row['area']} is logged. I'll show a return ramp only after a doctor or physio has cleared "
                "it; tell me when they have, and I'll mark it.")
    weeks = int((now - row["cleared_at"]) // WEEK)
    factor = RAMP[min(weeks, len(RAMP) - 1)]
    area = AREA_MUSCLES.get(row["area"], row["area"])
    return (f"Week {weeks + 1} back: use about {round(factor * 100)}% of your usual weights and sets on anything that "
            f"loads your {row['area']} ({area}), and stop and tell your physio if the pain returns. This is a load "
            "ramp on your own usual numbers, not rehab.")
