"""Macros left, morning note, weekly report, bedtime, sleep banking, load, normal ranges, progress (788 796 797 865 1004
1005 1025 890). Numbers are worked by hand; the wording is body-neutral."""
import re
import statistics
from datetime import datetime, timedelta, timezone

import pytest

from minty_core.coach.wellbeing import (bedtime_consistency, body_neutral_progress, macros_left, morning_note,
                                        normal_ranges, sleep_banking, stress_load, weekly_report)
from minty_core.health import HealthHub

DAY = 86400.0
NOW = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc).timestamp()
LINT = re.compile(r"(?i)\b(deficit|weight[- ]loss|lose weight|losing weight|fasting|weigh[- ]?in|bmi|diet plan|"
                  r"cutting|burn fat|calorie target)\b")


def hub(tmp_path):
    h = HealthHub(tmp_path / "h.db", clock=lambda: NOW)
    for d in ("training", "sleep", "body", "nutrition", "activity"):
        h.grant(d)
    h.add_body("health_connect", "w", NOW - DAY, "weight", 60.0, "kg")
    return h


def day(n):
    return datetime.fromtimestamp(NOW - n * DAY, timezone.utc).strftime("%Y-%m-%d")


def test_macros_left_today_uses_the_protein_band_and_your_usual_fuel(tmp_path):
    h = hub(tmp_path)
    h.add_nutrition("cronometer_csv", "t", day(0), 1500, 60, 180, 50, 1200)
    text = macros_left(h, day(0), maintenance_kcal=2200)
    assert "60 g" in text and "24 g more" in text and "84-120" in text  # 1.4-2.0 g/kg of 60 kg
    assert "700 kcal" in text and "2200" in text
    assert not LINT.search(text)
    empty = macros_left(h, day(5), maintenance_kcal=None)
    assert "nothing logged" in empty.lower()


def test_the_morning_note_gives_last_night_and_the_hint_with_its_basis(tmp_path):
    h = hub(tmp_path)
    for i in range(2, 16):
        h.add_sleep("health_connect", f"s{i}", NOW - i * DAY - 8 * 3600, NOW - i * DAY, 450)
    h.add_sleep("health_connect", "last", NOW - 9 * 3600, NOW - 3600, 330)
    h.add_nutrition("cronometer_csv", "y", day(1), 2300, 95, 280, 70, 2400)
    text = morning_note(h, NOW)
    assert "5.5 h" in text and "short on sleep" in text and "95 g protein yesterday" in text
    assert "not a health score" in text.lower() and not LINT.search(text)


def test_the_weekly_report_counts_what_it_is_based_on(tmp_path):
    h = hub(tmp_path)
    for i in range(1, 6):
        h.add_nutrition("cronometer_csv", f"n{i}", day(i), 2200, 100, 250, 70, 2000)
        h.add_sleep("health_connect", f"s{i}", NOW - i * DAY - 8 * 3600, NOW - i * DAY, 420)
    h.add_workout("hevy", "w", NOW - 2 * DAY, "Legs", 3600, [])
    text = weekly_report(h, NOW)
    assert "100 g" in text and "5 of 7 days" in text and "7.0 h" in text and "1 session" in text
    assert not LINT.search(text)


def test_bedtime_consistency_scores_the_spread(tmp_path):
    steady = [(NOW - i * DAY) // DAY * DAY + 23 * 3600 - DAY for i in range(1, 9)]
    s = bedtime_consistency(steady)
    assert s.score == 100 and s.spread_min == 0 and s.n == 8
    wobble = [(NOW - i * DAY) // DAY * DAY + (22 if i % 2 else 24) * 3600 - DAY for i in range(1, 9)]
    w = bedtime_consistency(wobble)
    assert w.spread_min == pytest.approx(60, abs=0.5) and w.score == 50
    assert bedtime_consistency(steady[:3]).score is None


def test_sleep_banking_before_a_heavy_week(tmp_path):
    plan = sleep_banking(avg_hours=7.0, upper_hours=9.0, days_until=5)
    assert "60 minutes" in plan and "5 nights" in plan
    assert "too close" in sleep_banking(7.0, 9.0, 1).lower()
    assert "already" in sleep_banking(8.8, 9.0, 5).lower()


def test_the_load_view_puts_training_next_to_sleep_and_the_exam_flag(tmp_path):
    h = hub(tmp_path)
    for w in range(1, 5):  # a usual 120 minutes a week
        h.add_workout("hevy", f"o{w}", NOW - (7 * w + 2) * DAY, "Any", 7200, [])
    for k in range(3):  # this week: 3 x 80 min = 240
        h.add_workout("hevy", f"n{k}", NOW - (k + 1) * DAY, "Any", 4800, [])
    for i in range(1, 8):
        h.add_sleep("health_connect", f"s{i}", NOW - i * DAY - 7 * 3600, NOW - i * DAY, 380)
    h.set_profile("exam_week", "1")
    text = stress_load(h, NOW)
    assert "240 min" in text and "120" in text and "high" in text and "6.3 h" in text and "exam" in text.lower()
    assert "easing" in text.lower() and not LINT.search(text)


def test_normal_ranges_are_your_own_and_say_how_much_data_they_stand_on(tmp_path):
    h = hub(tmp_path)
    nights = [420 + (30 if i % 2 else -30) for i in range(2, 28)]  # 26 nights around 7 h, inside the 28-day window
    for i, m in zip(range(2, 28), nights):
        h.add_sleep("health_connect", f"s{i}", NOW - i * DAY - 8 * 3600, NOW - i * DAY, m)
    h.add_sleep("health_connect", "last", NOW - 9 * 3600, NOW - 3600, 330)
    mean, sd = statistics.mean(nights) / 60, statistics.stdev(nights) / 60
    text = normal_ranges(h, NOW)
    assert f"{mean:.1f} h" in text and "26 nights" in text and "5.5 h" in text and "below" in text
    few = hub(tmp_path / "few")
    for i in range(1, 5):
        few.add_sleep("health_connect", f"s{i}", NOW - i * DAY - 8 * 3600, NOW - i * DAY, 400)
    assert "not enough" in normal_ranges(few, NOW).lower() and sd > 0


def test_body_neutral_progress_never_mentions_the_scale(tmp_path):
    h = hub(tmp_path)
    for i in range(6):
        h.add_workout("hevy", f"w{i}", NOW - (3 + 4 * i) * DAY, "Legs", 3600,
                      [{"exercise": "Squat (Barbell)", "idx": 0, "weight_kg": 100 + 2.5 * (6 - i), "reps": 5}])
    text = body_neutral_progress(h, NOW)
    assert "sessions" in text and "new estimated max" in text.lower()
    assert "60 kg" not in text and not re.search(r"(?i)\b(weight|bmi|body fat|lighter|heavier)\b", text.replace("weights", ""))
