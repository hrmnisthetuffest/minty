"""Nutrition and recovery numbers: protein band, maintenance from trends, hydration, sleep, readiness.

Every function says what it could not tell. Nothing here is advice to lose weight; a maintenance estimate is
information. A model only ever gets these results (a facts pack), never the logs they came from.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

DAY = 86400.0
KCAL_PER_KG = 7700.0
WINDOW_DAYS = 21
MIN_WEIGH_INS = 3
MIN_SPAN_DAYS = 10
MIN_FOOD_DAYS = 10
BASELINE_NIGHTS = 7
SD_FLOOR_MIN = 15.0


def protein_band(weight_kg: float) -> tuple[int, int] | None:
    """1.4-2.0 g per kg a day (ISSN position stand)."""
    if not weight_kg or weight_kg <= 0:
        return None
    return round(weight_kg * 1.4), round(weight_kg * 2.0)


def protein_status(avg_g: float, band: tuple[int, int]) -> str:
    return "below" if avg_g < band[0] else "above" if avg_g > band[1] else "in band"


@dataclass
class Maintenance:
    kcal: int | None
    confidence: str
    note: str


def maintenance_estimate(weights: list[tuple[float, float]], intake: list[tuple[str, float]],
                         now: float) -> Maintenance:
    """Mean intake minus what the weight trend says was stored (7700 kcal a kg). `intake` is the days in the window."""
    pts = sorted((t, kg) for t, kg in weights if now - WINDOW_DAYS * DAY <= t <= now)
    span = (pts[-1][0] - pts[0][0]) / DAY if len(pts) >= 2 else 0
    if len(pts) < MIN_WEIGH_INS or span < MIN_SPAN_DAYS or len(intake) < MIN_FOOD_DAYS:
        return Maintenance(None, "none",
                           f"I need at least {MIN_WEIGH_INS} weight entries over {MIN_SPAN_DAYS} days and {MIN_FOOD_DAYS} "
                           f"days of food logs; I have {len(pts)} weight entr{'ies' if len(pts) != 1 else 'y'} and "
                           f"{len(intake)} "
                           "days of food.")
    xs = [(t - pts[0][0]) / DAY for t, _ in pts]
    ys = [kg for _, kg in pts]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)  # kg per day
    mean_kcal = statistics.mean(k for _, k in intake)
    confidence = "medium" if len(pts) >= 4 and len(intake) >= 14 else "low"
    return Maintenance(round(mean_kcal - slope * KCAL_PER_KG), confidence,
                       "An estimate from your weight trend and food logs. Weight moves with water and salt, so "
                       "treat it as a rough guide, not a target.")


def sleep_target(age_band: str) -> tuple[float, float]:
    """Hours: 8-10 for teenagers, 7-9 for adults (AASM). Unknown age uses the adult range."""
    return (8.0, 10.0) if age_band == "under18" else (7.0, 9.0)


def sleep_status(avg_hours: float, target: tuple[float, float]) -> str:
    return "short" if avg_hours < target[0] else "long" if avg_hours > target[1] else "in range"


@dataclass
class Readiness:
    label: str  # well rested | normal | short on sleep | unknown
    z: float | None
    note: str


def readiness(nights: list[tuple[float, float]], now: float) -> Readiness:
    """Last night's sleep against your own previous 28 nights. A hint, not a health score."""
    recent = sorted((t, m) for t, m in nights if now - 28 * DAY <= t <= now)
    if not recent:
        return Readiness("unknown", None, "No sleep logged, so I can't give a hint.")
    last, base = recent[-1][1], [m for _, m in recent[:-1]]
    if len(base) < BASELINE_NIGHTS:
        return Readiness("unknown", None, f"I need at least {BASELINE_NIGHTS} nights to compare with; I have "
                                          f"{len(base)}.")
    sd = max(statistics.stdev(base), SD_FLOOR_MIN)
    z = (last - statistics.mean(base)) / sd
    label = "short on sleep" if z <= -1 else "well rested" if z >= 0.75 else "normal"
    return Readiness(label, round(z, 2), "A hint from your own sleep pattern, not a health score.")


def hydration_hint(avg_water_ml: float | None, weight_kg: float) -> str:
    if avg_water_ml is None:
        return "No water logged, so I can't say."
    guide = round(weight_kg * 35) if weight_kg else 2000
    if avg_water_ml < 0.75 * guide:
        return (f"Water logged averages {round(avg_water_ml)} ml, below the usual guide of about {guide} ml. It is "
                "a guide, not a target; food and weather count too.")
    return f"Water logged looks fine against the usual guide of about {guide} ml."


def recovery_hint(plateaued: int, short_sleep_nights: int) -> str:
    reasons = []
    if plateaued >= 2:
        reasons.append(f"{plateaued} lifts have stopped moving")
    if short_sleep_nights >= 5:
        reasons.append(f"{short_sleep_nights} of the last 7 nights were short")
    if not reasons:
        return ""
    return ("A lighter week may help (same exercises, about 60% of the usual sets, stay away from failure): "
            + " and ".join(reasons) + ".")
