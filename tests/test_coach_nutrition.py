"""Nutrition and recovery numbers (LF-08), worked by hand; every output says what it could not tell."""
import statistics

import pytest

from minty_core.coach.nutrition import (hydration_hint, maintenance_estimate, protein_band, protein_status,
                                        readiness, recovery_hint, sleep_status, sleep_target)

DAY = 86400.0
NOW = 1_800_000_000.0


def test_protein_band_is_1_4_to_2_0_g_per_kg():
    assert protein_band(62.4) == (87, 125)  # 87.36 and 124.8, rounded
    assert protein_band(80) == (112, 160)
    assert protein_band(0) is None


def test_protein_status_against_the_band():
    band = (87, 125)
    assert protein_status(70, band) == "below" and protein_status(100, band) == "in band"
    assert protein_status(140, band) == "above"


def intake(days, kcal):
    return [(f"2026-10-{d:02d}", kcal) for d in range(1, days + 1)]


def test_maintenance_from_the_weight_trend_and_intake():
    # +0.6 kg over 21 days = 0.028571 kg/day * 7700 = 220 kcal/day of surplus; eating 2400 -> maintenance 2180
    weights = [(NOW - 21 * DAY, 60.0), (NOW - 14 * DAY, 60.2), (NOW - 7 * DAY, 60.4), (NOW, 60.6)]
    est = maintenance_estimate(weights, intake(14, 2400), NOW)
    assert est.kcal == pytest.approx(2180, abs=2)
    assert est.confidence in ("low", "medium") and "estimate" in est.note.lower()


def test_too_little_data_gives_no_number_and_says_what_is_missing():
    est = maintenance_estimate([(NOW, 60.0)], intake(4, 2400), NOW)
    assert est.kcal is None and "weight entries" in est.note and "food" in est.note


def test_sleep_targets_by_age_band_and_the_status():
    assert sleep_target("under18") == (8.0, 10.0) and sleep_target("adult") == (7.0, 9.0)
    assert sleep_target("unknown") == (7.0, 9.0)
    assert sleep_status(7.2, (8.0, 10.0)) == "short" and sleep_status(8.5, (8.0, 10.0)) == "in range"
    assert sleep_status(11, (8.0, 10.0)) == "long"


def test_readiness_compares_last_night_with_your_own_month():
    nights = [(NOW - (i + 1) * DAY, 420 if i % 2 else 480) for i in range(27)]
    mean = statistics.mean(m for _, m in nights)
    sd = statistics.stdev(m for _, m in nights)
    short = readiness(nights + [(NOW - 3600, 360)], NOW)
    assert short.label == "short on sleep" and short.z == pytest.approx((360 - mean) / sd, abs=0.05)
    normal = readiness(nights + [(NOW - 3600, 455)], NOW)
    assert normal.label == "normal"
    assert "hint" in normal.note.lower() and "not a health score" in normal.note.lower()


def test_readiness_needs_a_baseline():
    r = readiness([(NOW - 3600, 400)] * 3, NOW)
    assert r.label == "unknown" and "nights" in r.note


def test_hydration_hint_is_a_guide_not_a_diagnosis():
    assert "below" in hydration_hint(1500, 62.4) and "guide" in hydration_hint(1500, 62.4).lower()
    assert hydration_hint(None, 62.4) == "No water logged, so I can't say."
    assert "below" not in hydration_hint(2600, 62.4)


def test_a_deload_is_suggested_for_two_plateaus_or_a_week_of_short_sleep():
    assert "lighter week" in recovery_hint(plateaued=2, short_sleep_nights=0)
    assert "lighter week" in recovery_hint(plateaued=0, short_sleep_nights=6)
    assert recovery_hint(plateaued=1, short_sleep_nights=2) == ""
