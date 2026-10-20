"""The health data hub: one local store, origin and residency on every row, consent per domain (LF-01, LF-02)."""
import stat
from datetime import datetime, timezone

import pytest

from minty_core.health import ConsentOff, HealthHub
from minty_core.residency import LOCAL_ONLY, SENSITIVE

DAY = datetime(2026, 10, 12, 7, 30, tzinfo=timezone.utc).timestamp()


def hub(tmp_path, grant=("training", "body", "sleep", "nutrition", "activity")):
    h = HealthHub(tmp_path / "health.db", clock=lambda: DAY + 86400)
    for domain in grant:
        h.grant(domain)
    return h


def workout(h, ext="w1", origin="hevy", at=DAY, sets=None):
    return h.add_workout(origin, ext, at, "Push day", 3600,
                         sets or [{"exercise": "Bench Press", "idx": 0, "weight_kg": 60, "reps": 8},
                                  {"exercise": "Bench Press", "idx": 1, "weight_kg": 60, "reps": 7}])


def test_nothing_is_collected_until_a_domain_is_agreed(tmp_path):
    h = HealthHub(tmp_path / "health.db")
    with pytest.raises(ConsentOff) as why:
        workout(h)
    assert why.value.domain == "training"
    assert h.counts() == {d: 0 for d in ("training", "body", "sleep", "nutrition", "activity")}


def test_the_same_record_twice_is_one_record_and_origins_stay_apart(tmp_path):
    h = hub(tmp_path)
    assert workout(h) is True and workout(h) is False
    assert workout(h, origin="strava_export") is True  # same id from another origin is another record
    assert h.counts()["training"] == 2
    assert h.add_body("health_connect", "b1", DAY, "weight", 62.4, "kg") is True
    assert h.add_body("health_connect", "b1", DAY, "weight", 62.4, "kg") is False


def test_every_row_carries_its_origin_and_residency(tmp_path):
    h = hub(tmp_path)
    workout(h)
    h.add_activity("strava_api", "a1", DAY, "Run", 10400, 3133, 161, 40)
    rows = {r["origin"]: r["residency"] for r in h.rows("workouts") + h.rows("activities")}
    assert rows == {"hevy": SENSITIVE, "strava_api": LOCAL_ONLY}


def test_revoking_a_domain_stops_collection_and_offers_deletion_but_keeps_the_data(tmp_path):
    h = hub(tmp_path)
    workout(h)
    message = h.revoke("training")
    assert "stopped" in message and "delete" in message and "1" in message
    with pytest.raises(ConsentOff):
        workout(h, ext="w2")
    assert h.counts()["training"] == 1
    assert h.delete_domain("training") == 1 and h.counts()["training"] == 0
    assert h.rows("sets") == []  # the sets went with their workout


def test_profile_age_band_and_limits(tmp_path):
    h = hub(tmp_path)
    assert h.age_band() == "unknown"
    h.set_profile("age", "15")
    assert h.age_band() == "under18" and h.profile()["age"] == "15"
    h.set_profile("age", "34")
    assert h.age_band() == "adult"
    with pytest.raises(ValueError):
        h.set_profile("age", "2")
    with pytest.raises(ValueError):
        h.set_profile("favourite_colour", "blue")
    h.set_profile("goals", "x" * 1000)
    assert len(h.profile()["goals"]) == 300


def test_the_file_is_owner_only_and_a_file_is_remembered_by_its_hash(tmp_path):
    h = hub(tmp_path)
    assert stat.S_IMODE((tmp_path / "health.db").stat().st_mode) == 0o600
    assert h.seen_file("abc") is False
    h.log_import("abc", "hevy", rows=10, added=10)
    assert h.seen_file("abc") is True


def test_a_facts_pack_is_small_and_has_only_aggregates(tmp_path):
    h = hub(tmp_path)
    for i in range(3):
        workout(h, ext=f"w{i}", at=DAY - i * 86400 * 2)
    h.add_body("health_connect", "b1", DAY, "weight", 62.4, "kg")
    h.add_sleep("health_connect", "s1", DAY - 8 * 3600, DAY - 1 * 3600, 420)
    h.add_nutrition("cronometer_csv", "n1", "2026-10-11", 2400, 110, 300, 70, 2500)
    pack = h.facts_pack(days=7)
    assert len(pack) < 900 and "3 workouts" in pack and "Bench Press" in pack
    assert "62.4" in pack and "7.0 h" in pack and "2400" in pack
    assert "w0" not in pack and "Push day" not in pack  # no row-level detail, no ids, no titles


def test_local_only_rows_never_appear_in_a_facts_pack(tmp_path):
    h = hub(tmp_path)
    h.add_activity("strava_api", "a1", DAY, "Run", 10400, 3133, 161, 40)
    h.add_activity("strava_export", "a2", DAY, "Ride", 20000, 3600, 130, 100)
    h.add_workout("strava_api", "w9", DAY, "Hidden", 60, [{"exercise": "Secret lift", "idx": 0, "weight_kg": 100, "reps": 5}])
    pack = h.facts_pack(days=7)
    assert "1 sessions, 20.0 km" in pack and "10.4" not in pack and "Secret lift" not in pack
