"""The data quality doctor (965) and the Hevy housekeeping report (984): read-only.

It looks for gaps, duplicates, records with a guessed timezone or a date in the future, and values no person has
(a weight of 6 kg, a 900 kg deadlift). It never diagnoses anything and never changes a record: it says what to look at,
so a coach does not build advice on a typo.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from minty_core.health import HealthHub

GAP_DAYS = {"sleep": 3, "body": 14, "nutrition": 3, "training": 14, "activity": 21}
DUPLICATE_S = 120
HEVY_DUPLICATE_S = 300
PLAUSIBLE = {"weight": (25.0, 300.0), "body_fat": (2.0, 70.0)}


@dataclass
class Issue:
    kind: str  # gap | duplicate | timezone | future | implausible | empty | short
    domain: str
    detail: str
    count: int = 1


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _gaps(domain: str, stamps: list[float]) -> list[Issue]:
    days = sorted({datetime.fromtimestamp(t, timezone.utc).date() for t in stamps})
    out = []
    for a, b in zip(days, days[1:]):
        missing = (b - a).days - 1
        if missing >= GAP_DAYS[domain]:
            out.append(Issue("gap", domain, f"{missing} days without {domain} data between {a} and {b}", missing))
    return out


def quality_report(hub: HealthHub, now: float) -> list[Issue]:
    out: list[Issue] = []
    workouts, sets = hub.rows("workouts"), hub.rows("sets")
    body, sleep = hub.rows("body"), hub.rows("sleep")
    nutrition, acts = hub.rows("nutrition"), hub.rows("activities")

    # gaps
    for domain, stamps in (("sleep", [r["start"] for r in sleep]), ("body", [r["ts"] for r in body]),
                           ("training", [r["started"] for r in workouts]),
                           ("activity", [r["started"] for r in acts]),
                           ("nutrition", [datetime.strptime(r["day"], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
                                          for r in nutrition])):
        out += _gaps(domain, stamps)

    # duplicates
    def near(rows, key, window, same=lambda a, b: True):
        rows = sorted(rows, key=lambda r: r[key])
        return [(a, b) for a, b in zip(rows, rows[1:])
                if b[key] - a[key] <= window and (a["origin"], a["ext_id"]) != (b["origin"], b["ext_id"]) and same(a, b)]

    pairs = near(workouts, "started", DUPLICATE_S)
    if pairs:
        out.append(Issue("duplicate", "training", f"{len(pairs)} workout{'s' * (len(pairs) != 1)} that look recorded "
                                                  "twice (same start time)", len(pairs)))
    pairs = near(acts, "started", DUPLICATE_S, lambda a, b: a["kind"].lower() == b["kind"].lower())
    if pairs:
        out.append(Issue("duplicate", "activity", f"{len(pairs)} activities that look recorded twice", len(pairs)))
    pairs = near(body, "ts", 60, lambda a, b: a["kind"] == b["kind"] and a["value"] == b["value"])
    if pairs:
        out.append(Issue("duplicate", "body", f"{len(pairs)} measurements saved twice", len(pairs)))
    overlaps = 0
    srt = sorted(sleep, key=lambda r: r["start"])
    for a, b in zip(srt, srt[1:]):
        shorter = min(a["end"] - a["start"], b["end"] - b["start"])
        if shorter > 0 and (min(a["end"], b["end"]) - b["start"]) > shorter / 2:
            overlaps += 1
    if overlaps:
        out.append(Issue("duplicate", "sleep", f"{overlaps} sleep sessions overlap", overlaps))

    # timezone and future
    for domain, rows, key in (("training", workouts, "started"), ("body", body, "ts"), ("sleep", sleep, "start"),
                              ("activity", acts, "started")):
        guessed = sum(1 for r in rows if r["tz_assumed"])
        if guessed:
            out.append(Issue("timezone", domain, f"{guessed} {domain} record{'s' * (guessed != 1)} had no timezone; "
                                                 "I assumed this computer's", guessed))
        future = sum(1 for r in rows if r[key] > now + 86400)
        if future:
            out.append(Issue("future", domain, f"{future} {domain} record{'s' * (future != 1)} dated in the future",
                             future))

    # values nobody has
    bad = sum(1 for r in body if r["kind"] in PLAUSIBLE and not PLAUSIBLE[r["kind"]][0] <= r["value"] <= PLAUSIBLE[r["kind"]][1])
    if bad:
        out.append(Issue("implausible", "body", f"{bad} body measurement{'s' * (bad != 1)} outside what is possible "
                                                "(a typo or a unit mix-up?)", bad))
    bad = sum(1 for r in sleep if r["minutes"] > 16 * 60 or r["minutes"] < 20)
    if bad:
        out.append(Issue("implausible", "sleep", f"{bad} sleep session{'s' * (bad != 1)} under 20 minutes or over 16 "
                                                 "hours", bad))
    bad = sum(1 for s in sets if (s["weight_kg"] or 0) > 600 or (s["reps"] or 0) > 100)
    bad += sum(1 for w in workouts if (w["duration_s"] or 0) > 6 * 3600)
    if bad:
        out.append(Issue("implausible", "training", f"{bad} set or workout value{'s' * (bad != 1)} that look like "
                                                    "typos (over 600 kg, over 100 reps, over 6 hours)", bad))
    bad = sum(1 for r in nutrition if (r["kcal"] is not None and not 200 <= r["kcal"] <= 10000)
              or (r["protein_g"] or 0) > 500)
    if bad:
        out.append(Issue("implausible", "nutrition", f"{bad} food day{'s' * (bad != 1)} with calories or protein no "
                                                     "one eats (a logging slip?)", bad))
    bad = sum(1 for r in acts if (r["avg_hr"] is not None and not 30 <= r["avg_hr"] <= 220)
              or (r["distance_m"] or 0) > 300_000)
    if bad:
        out.append(Issue("implausible", "activity", f"{bad} activit{'ies' if bad != 1 else 'y'} with a heart rate or "
                                                    "distance that cannot be right", bad))
    return out


def summarize(issues: list[Issue]) -> str:
    if not issues:
        return "I found nothing odd in your health data."
    lines = "; ".join(i.detail for i in issues[:4])
    more = f" And {len(issues) - 4} more." if len(issues) > 4 else ""
    n = len(issues)
    return f"I found {n} thing{'s' * (n != 1)} worth a look: {lines}.{more} I haven't changed anything."


def housekeeping_report(hub: HealthHub) -> list[Issue]:
    """Hevy-origin workouts that look re-saved, empty or tiny. Read-only."""
    workouts = [w for w in hub.rows("workouts") if w["origin"] == "hevy"]
    with_sets = {s["workout_id"] for s in hub.rows("sets")}
    out: list[Issue] = []
    ordered = sorted(workouts, key=lambda w: w["started"])
    dups = [(a, b) for a, b in zip(ordered, ordered[1:])
            if b["started"] - a["started"] <= HEVY_DUPLICATE_S and a["title"] == b["title"]]
    if dups:
        out.append(Issue("duplicate", "training", f"{len(dups)} workout{'s' * (len(dups) != 1)} saved twice in Hevy "
                                                  "(same title, minutes apart)", len(dups)))
    empty = [w for w in workouts if w["id"] not in with_sets]
    if empty:
        out.append(Issue("empty", "training", f"{len(empty)} workout{'s' * (len(empty) != 1)} with no sets logged",
                         len(empty)))
    short = [w for w in workouts if w["duration_s"] is not None and w["duration_s"] < 300]
    if short:
        out.append(Issue("short", "training", f"{len(short)} workout{'s' * (len(short) != 1)} under five minutes",
                         len(short)))
    return out
