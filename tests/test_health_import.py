"""Importers: Hevy CSV/JSON, Cronometer CSV, Strava archive CSV, Health Connect JSON/CSV (LF-03..LF-06)."""
import json

import pytest

from minty_core.health import HealthHub
from minty_core.health_import import ImportRejected, import_bytes, sniff

HEVY_CSV = (
    "title,start_time,end_time,description,exercise_title,superset_id,exercise_notes,set_index,set_type,weight_kg,"
    "reps,distance_km,duration_seconds,rpe\n"
    '"Push day","12 Oct 2026, 07:30","12 Oct 2026, 08:30","","Bench Press (Barbell)",,"",0,normal,60,8,,,8\n'
    '"Push day","12 Oct 2026, 07:30","12 Oct 2026, 08:30","","Bench Press (Barbell)",,"",1,normal,60,7,,,9\n'
    '"Push day","12 Oct 2026, 07:30","12 Oct 2026, 08:30","","Overhead Press",,"",0,normal,35,10,,,\n'
    '"Legs","14 Oct 2026, 07:30","14 Oct 2026, 08:45","","Squat (Barbell)",,"",0,warmup,40,10,,,\n'
    '"Legs","14 Oct 2026, 07:30","14 Oct 2026, 08:45","","Squat (Barbell)",,"",1,normal,80,5,,,8\n'
)
HEVY_JSON = json.dumps({"page": 1, "page_count": 1, "workouts": [{
    "id": "abc-123", "title": "Pull day", "start_time": "2026-10-13T07:00:00+05:30",
    "end_time": "2026-10-13T08:00:00+05:30",
    "exercises": [{"title": "Barbell Row", "exercise_template_id": "t1", "sets": [
        {"index": 0, "type": "normal", "weight_kg": 50, "reps": 10, "rpe": 7},
        {"index": 1, "type": "normal", "weight_kg": 50, "reps": 9, "rpe": 8}]}]}]})
CRONOMETER = (
    "Date,Energy (kcal),Alcohol (g),Caffeine (mg),Water (g),Carbs (g),Fat (g),Protein (g),Completed\n"
    "2026-10-11,2400,0,80,2500,300,70,110,true\n2026-10-12,2650.5,0,40,3000,340,80,125,true\n")
STRAVA = (
    "Activity ID,Activity Date,Activity Name,Activity Type,Elapsed Time,Distance,Max Heart Rate,Elapsed Time,"
    "Moving Time,Distance,Elevation Gain,Average Heart Rate\n"
    '11,"Oct 12, 2026, 2:00:00 AM",Morning Run,Run,3133,10.4,178,3133,3100,10412.3,41,161\n'
    '12,"Oct 14, 2026, 1:00:00 AM",Easy Ride,Ride,3600,20.0,150,3600,3500,20010.0,120,130\n')
HEALTH_CONNECT = json.dumps({"records": [
    {"id": "hc1", "type": "weight", "start": "2026-10-12T06:00:00+05:30", "value": 62.4, "unit": "kg"},
    {"id": "hc2", "type": "sleep", "start": "2026-10-11T23:00:00+05:30", "end": "2026-10-12T06:30:00+05:30"},
    {"id": "hc3", "type": "nutrition", "start": "2026-10-12T00:00:00+05:30", "kcal": 2500, "protein_g": 120,
     "carbs_g": 310, "fat_g": 75, "water_ml": 2800},
    {"id": "hc4", "type": "exercise", "start": "2026-10-12T17:00:00+05:30", "end": "2026-10-12T17:45:00+05:30",
     "kind": "Cycling", "distance_m": 12000, "avg_hr": 128},
    {"id": "hc5", "type": "steps", "start": "2026-10-12T00:00:00+05:30", "value": 9000}]})


def hub(tmp_path, grant=("training", "body", "sleep", "nutrition", "activity")):
    h = HealthHub(tmp_path / "health.db")
    for d in grant:
        h.grant(d)
    return h


def test_a_hevy_csv_becomes_workouts_with_their_sets_and_again_changes_nothing(tmp_path):
    h = hub(tmp_path)
    report = import_bytes(h, HEVY_CSV.encode(), "workouts.csv")
    assert (report.origin, report.added) == ("hevy", 2) and h.counts()["training"] == 2
    assert len(h.rows("sets")) == 5 and {r["exercise"] for r in h.rows("sets")} >= {"Bench Press (Barbell)"}
    assert next(r for r in h.rows("sets") if r["kind"] == "warmup")["weight_kg"] == 40
    again = import_bytes(h, HEVY_CSV.encode(), "workouts.csv")
    assert again.already_imported is True and h.counts()["training"] == 2 and len(h.rows("sets")) == 5


def test_a_changed_export_adds_only_what_is_new(tmp_path):
    h = hub(tmp_path)
    import_bytes(h, HEVY_CSV.encode(), "a.csv")
    more = HEVY_CSV + '"Pull","16 Oct 2026, 07:30","16 Oct 2026, 08:30","","Row",,"",0,normal,50,10,,,\n'
    report = import_bytes(h, more.encode(), "b.csv")
    assert report.added == 1 and report.duplicates == 2 and h.counts()["training"] == 3


def test_the_hevy_api_shape_is_read_too(tmp_path):
    h = hub(tmp_path)
    report = import_bytes(h, HEVY_JSON.encode(), "hevy.json")
    assert report.origin == "hevy" and report.added == 1
    row = h.rows("workouts")[0]
    assert row["ext_id"] == "abc-123" and row["tz_assumed"] == 0
    assert [s["reps"] for s in h.rows("sets")] == [10, 9]


def test_cronometer_daily_totals(tmp_path):
    h = hub(tmp_path)
    report = import_bytes(h, CRONOMETER.encode(), "dailysummary.csv")
    assert report.origin == "cronometer_csv" and report.added == 2
    row = h.rows("nutrition")[1]
    assert (row["day"], row["kcal"], row["protein_g"], row["water_ml"]) == ("2026-10-12", 2650.5, 125.0, 3000.0)


def test_a_strava_archive_is_sensitive_not_local_only(tmp_path):
    h = hub(tmp_path)
    report = import_bytes(h, STRAVA.encode(), "activities.csv")
    assert report.origin == "strava_export" and report.added == 2
    run = h.rows("activities")[0]
    assert run["kind"] == "Run" and run["distance_m"] == pytest.approx(10412.3) and run["avg_hr"] == 161
    assert run["residency"] == "personal_sensitive"


def test_health_connect_records_go_to_their_tables_and_unknown_types_are_counted(tmp_path):
    h = hub(tmp_path)
    report = import_bytes(h, HEALTH_CONNECT.encode(), "export.json")
    assert report.origin == "health_connect" and report.added == 4 and report.ignored == 1
    counts = h.counts()
    assert (counts["body"], counts["sleep"], counts["nutrition"], counts["activity"]) == (1, 1, 1, 1)
    assert h.rows("sleep")[0]["minutes"] == 450


def test_a_domain_without_consent_is_skipped_and_counted_not_an_error(tmp_path):
    h = hub(tmp_path, grant=("training",))
    report = import_bytes(h, HEALTH_CONNECT.encode(), "export.json")
    assert report.added == 0 and report.skipped_consent == {"body": 1, "sleep": 1, "nutrition": 1, "activity": 1}
    assert "not agreed" in report.text() or "turned off" in report.text()


@pytest.mark.parametrize("data,name,why", [
    (b"this is not a health export at all", "notes.txt", "doesn't look like"),
    (b'{"workouts": [', "x.json", "not valid JSON"),
    (b"title,start_time\nPush,12 Oct 2026\n", "w.csv", "missing"),
    (b"\x00\x01\x02\xff" * 50, "w.csv", "doesn't look like"),
    (b'{"records": "nope"}', "x.json", "doesn't look like"),
])
def test_what_is_not_an_export_is_rejected_with_a_reason(tmp_path, data, name, why):
    h = hub(tmp_path)
    with pytest.raises(ImportRejected) as e:
        import_bytes(h, data, name)
    assert why in str(e.value)
    assert sum(h.counts().values()) == 0


def test_a_file_that_is_mostly_unreadable_is_refused_whole(tmp_path):
    h = hub(tmp_path)
    bad = CRONOMETER.splitlines()[0] + "\n" + "\n".join(f"not-a-date-{i},1,0,0,0,0,0,0,true" for i in range(8))
    bad += "\n2026-10-11,2400,0,80,2500,300,70,110,true\n"
    with pytest.raises(ImportRejected) as e:
        import_bytes(h, bad.encode(), "daily.csv")
    assert "unreadable" in str(e.value) and h.counts()["nutrition"] == 0


def test_sniffing_picks_the_format_from_the_content(tmp_path):
    assert sniff(HEVY_CSV.encode(), "x.csv") == "hevy_csv"
    assert sniff(HEVY_JSON.encode(), "x.json") == "hevy_json"
    assert sniff(CRONOMETER.encode(), "x.csv") == "cronometer_csv"
    assert sniff(STRAVA.encode(), "activities.csv") == "strava_csv"
    assert sniff(HEALTH_CONNECT.encode(), "x.json") == "health_connect"
    assert sniff(b"name,price\nmilk,30\n", "list.csv") is None
