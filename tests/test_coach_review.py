"""Plateau post-mortem, what works for you, injury log and return ramp, air quality (WP-28, 983 981 784 856)."""
import httpx
import pytest

from minty_core.coach.air import air_note
from minty_core.coach.planning import what_works
from minty_core.coach.review import injury_ramp, plateau_postmortem, weekly_series
from minty_core.health import HealthHub

DAY = 86400.0
NOW = 1_800_000_000.0
SQ = "Squat (Barbell)"


def hub(tmp_path):
    h = HealthHub(tmp_path / "h.db", clock=lambda: NOW)
    for d in ("training", "sleep", "body", "nutrition", "activity"):
        h.grant(d)
    return h


def stalled(h, sleep=True, food=True):
    for i, days_ago in enumerate(range(80, 0, -7)):  # a squat session a week for 11 weeks
        w = 100 + (2.5 * i if days_ago > 45 else 7.5)  # rises, then flat at 107.5 for the last six weeks
        h.add_workout("hevy", f"s{i}", NOW - days_ago * DAY, "Legs", 3600,
                      [{"exercise": SQ, "idx": k, "weight_kg": w, "reps": 5} for k in range(3)])
    if sleep:
        for d in range(1, 15):
            h.add_sleep("health_connect", f"sl{d}", NOW - d * DAY - 8 * 3600, NOW - d * DAY, 400)
    if food:
        for d in range(1, 11):
            from datetime import datetime
            h.add_nutrition("cronometer_csv", f"n{d}", datetime.fromtimestamp(NOW - d * DAY).strftime("%Y-%m-%d"),
                            2300, 60, 280, 70, 2000)
    h.add_body("health_connect", "w1", NOW - 30 * DAY, "weight", 62.0, "kg")
    h.add_body("health_connect", "w2", NOW - 2 * DAY, "weight", 62.4, "kg")


def test_the_post_mortem_states_measured_numbers_and_lists_what_is_missing(tmp_path):
    h = hub(tmp_path)
    stalled(h)
    text = plateau_postmortem(h, SQ, NOW)
    assert "6 sessions" in text or "7 sessions" in text
    assert "6.7 h" in text  # 400 minutes of sleep on the nights logged
    assert "60 g" in text and "87" in text  # logged protein against the 1.4-2.0 g/kg band for 62.4 kg
    assert "62.4" in text
    for guess in ("probably", "maybe", "might be", "likely"):
        assert guess not in text.lower()
    assert "sleep was logged on 14 of the last 42 days" in text  # a gap, stated
    assert "sessions a week" not in text or "target" in text


def test_a_missing_data_source_is_a_gap_not_a_guess(tmp_path):
    h = hub(tmp_path)
    stalled(h, sleep=False, food=False)
    text = plateau_postmortem(h, SQ, NOW)
    assert "no sleep" in text.lower() and "no food" in text.lower() and "Data gaps" in text
    assert " h of sleep" not in text and "g protein" not in text


def test_what_works_needs_eight_weeks_and_never_claims_cause():
    short = what_works([{"sessions": 3, "e1rm_change": 1.0, "sleep_h": 7, "protein_g": 90}] * 5)
    assert "at least 8 weeks" in short
    weeks = [{"sessions": s, "e1rm_change": s * 0.8 - 1, "sleep_h": 7.0 + (i % 3) * 0.1, "protein_g": None}
             for i, s in enumerate([2, 3, 4, 2, 3, 4, 2, 3, 4, 3])]
    text = what_works(weeks)
    assert "more sessions per week" in text and "tended to go with" in text
    assert "protein" in text and "Not enough weeks" in text
    assert "not proof of cause" in text


def test_weekly_series_builds_the_inputs_for_it(tmp_path):
    h = hub(tmp_path)
    stalled(h)
    series = weekly_series(h, NOW, weeks=12)
    assert len(series) == 12 and all({"sessions", "e1rm_change", "sleep_h", "protein_g"} <= set(w) for w in series)
    assert sum(w["sessions"] for w in series) >= 8


def test_an_injury_is_logged_and_the_ramp_waits_for_a_clearance(tmp_path):
    h = hub(tmp_path)
    iid = h.log_injury("knee", "twinge on the last rep of squats")
    assert [i["area"] for i in h.injuries(active_only=True)] == ["knee"]
    before = injury_ramp(h, iid, NOW)
    assert "cleared" in before and "%" not in before  # no load numbers until a doctor or physio cleared it
    h.clear_injury(iid)
    after = injury_ramp(h, iid, NOW)
    assert "50%" in after and "physio" in after.lower() and "rehab" not in after.lower().replace("not rehab", "")
    assert h.injuries(active_only=True) == []


async def test_air_quality_is_a_note_with_a_threshold_and_no_key():
    def handler(request):
        assert "air-quality-api.open-meteo.com" in str(request.url) and "key" not in str(request.url).lower()
        return httpx.Response(200, json={"current": {"us_aqi": 162, "pm2_5": 71.0}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    note = await air_note(client, 30.7, 76.7)
    assert "162" in note and "indoors" in note.lower()
    ok = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"current": {"us_aqi": 35, "pm2_5": 8.0}})))
    assert "fine" in (await air_note(ok, 30.7, 76.7)).lower()
    down = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert "couldn't" in (await air_note(down, 30.7, 76.7)).lower()
