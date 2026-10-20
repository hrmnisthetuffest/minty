"""Data quality doctor (965) and Hevy housekeeping (984): find the problems before a coach reads the data."""
from datetime import datetime, timedelta, timezone

from minty_core.health import HealthHub
from minty_core.health_quality import housekeeping_report, quality_report

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def at(days_ago, hour=7, minute=0):
    return (NOW - timedelta(days=days_ago)).replace(hour=hour, minute=minute).timestamp()


def hub(tmp_path):
    h = HealthHub(tmp_path / "health.db", clock=NOW.timestamp)
    for d in ("training", "body", "sleep", "nutrition", "activity"):
        h.grant(d)
    return h


def kinds(report):
    return {i.kind for i in report}


def test_a_clean_hub_has_no_findings(tmp_path):
    h = hub(tmp_path)
    for i in range(5):
        h.add_sleep("health_connect", f"s{i}", at(i + 1, 23) - 86400 + 0, at(i + 1, 23) - 86400 + 7 * 3600, 420)
        h.add_workout("hevy", f"w{i}", at(i + 1), "Push", 3600, [{"exercise": "Bench", "idx": 0, "weight_kg": 60, "reps": 8}])
    assert quality_report(h, NOW.timestamp()) == []


def test_missing_days_are_reported_as_gaps(tmp_path):
    h = hub(tmp_path)
    for days_ago in (30, 29, 28, 20, 19):  # nothing between day 28 and day 20
        h.add_sleep("health_connect", f"s{days_ago}", at(days_ago, 0), at(days_ago, 7), 420)
    found = [i for i in quality_report(h, NOW.timestamp()) if i.kind == "gap" and i.domain == "sleep"]
    assert len(found) == 1 and "7 days" in found[0].detail


def test_two_copies_of_one_workout_are_flagged_as_possible_duplicates(tmp_path):
    h = hub(tmp_path)
    sets = [{"exercise": "Squat", "idx": 0, "weight_kg": 80, "reps": 5}]
    h.add_workout("hevy", "a", at(2), "Legs", 3600, sets)
    h.add_workout("health_connect", "b", at(2) + 60, "Strength", 3500, sets)
    found = [i for i in quality_report(h, NOW.timestamp()) if i.kind == "duplicate"]
    assert found and found[0].domain == "training"


def test_records_with_no_timezone_or_from_the_future_are_called_out(tmp_path):
    h = hub(tmp_path)
    h.add_workout("hevy", "a", at(3), "Legs", 3600, [], tz_assumed=True)
    h.add_workout("hevy", "b", at(-5), "Tomorrow?", 3600, [])
    report = quality_report(h, NOW.timestamp())
    assert "timezone" in kinds(report) and "future" in kinds(report)


def test_implausible_values_are_found_without_naming_a_diagnosis(tmp_path):
    h = hub(tmp_path)
    h.add_body("health_connect", "b1", at(1), "weight", 6.2, "kg")  # a decimal slip
    h.add_sleep("health_connect", "s1", at(2, 0), at(2, 0) + 20 * 3600, 1200)
    h.add_workout("hevy", "w", at(3), "Max", 3600, [{"exercise": "Deadlift", "idx": 0, "weight_kg": 900, "reps": 3}])
    h.add_nutrition("cronometer_csv", "n", "2026-10-18", 25000, 100, 100, 50, 2000)
    found = [i for i in quality_report(h, NOW.timestamp()) if i.kind == "implausible"]
    assert {i.domain for i in found} == {"body", "sleep", "training", "nutrition"}
    assert all("disease" not in i.detail.lower() and "diagnos" not in i.detail.lower() for i in found)


def test_the_report_reads_as_a_short_summary(tmp_path):
    h = hub(tmp_path)
    h.add_body("health_connect", "b1", at(1), "weight", 6.2, "kg")
    from minty_core.health_quality import summarize

    text = summarize(quality_report(h, NOW.timestamp()))
    assert "1 thing" in text and len(text) < 500
    assert summarize([]) == "I found nothing odd in your health data."


def test_hevy_housekeeping_finds_duplicates_empty_and_tiny_sessions_and_changes_nothing(tmp_path):
    h = hub(tmp_path)
    one = [{"exercise": "Row", "idx": 0, "weight_kg": 50, "reps": 10}]
    h.add_workout("hevy", "a", at(2), "Pull", 3600, one)
    h.add_workout("hevy", "b", at(2) + 120, "Pull", 3600, one)  # re-saved two minutes later
    h.add_workout("hevy", "c", at(4), "Empty", 1800, [])
    h.add_workout("hevy", "d", at(5), "Oops", 90, one)  # a 90-second "workout"
    h.add_workout("health_connect", "e", at(6), "Other origin", 3600, [])  # not Hevy: not this report's business
    before = h.counts()
    report = housekeeping_report(h)
    assert {i.kind for i in report} == {"duplicate", "empty", "short"}
    assert h.counts() == before  # read-only
