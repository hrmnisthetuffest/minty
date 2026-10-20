"""Pre-workout brief and post-workout debrief (LF-10): built from the hub, silent during a session."""
import pytest

from minty_core.coach.briefs import Coach, next_title
from minty_core.health import HealthHub

DAY = 86400.0
NOW = 1_800_000_000.0


def hub(tmp_path):
    h = HealthHub(tmp_path / "health.db", clock=lambda: NOW)
    for d in ("training", "sleep", "body", "nutrition", "activity"):
        h.grant(d)
    return h


def add(h, ext, days_ago, title, sets, dur=3600, hour_offset=0):
    h.add_workout("hevy", ext, NOW - days_ago * DAY + hour_offset, title, dur,
                  [{"exercise": ex, "idx": i, "weight_kg": w, "reps": r} for i, (ex, w, r) in enumerate(sets)])


def rotation(h):
    plan = [(20, "Push", [("Bench Press (Barbell)", 80, 8)] * 3), (18, "Pull", [("Bent Over Row", 60, 8)] * 3),
            (16, "Legs", [("Squat (Barbell)", 100, 8)] * 3, 3600), (13, "Push", [("Bench Press (Barbell)", 80, 8)] * 3),
            (11, "Pull", [("Bent Over Row", 60, 8)] * 3), (9, "Legs", [("Squat (Barbell)", 100, 8)] * 3, 4200),
            (6, "Push", [("Bench Press (Barbell)", 82.5, 6)] * 3), (4, "Pull", [("Bent Over Row", 62.5, 6)] * 3),
            (2, "Legs", [("Squat (Barbell)", 100, 8)] * 3, 3000)]
    for i, row in enumerate(plan):
        add(h, f"w{i}", row[0], row[1], row[2], *(row[3:] or ()))


def test_the_next_session_follows_your_own_rotation():
    assert next_title(["Push", "Pull", "Legs", "Push", "Pull", "Legs"]) == "Push"
    assert next_title(["Push", "Pull", "Legs", "Push"]) == "Pull"
    assert next_title(["Upper"]) == "Upper" and next_title([]) is None


def test_the_pre_workout_brief_has_the_routine_targets_warmup_and_time(tmp_path):
    h = hub(tmp_path)
    rotation(h)
    brief = Coach(h, clock=lambda: NOW).pre_workout()
    text = brief.text
    assert "Push" in text  # Legs was last
    assert "Bench Press (Barbell)" in text and "reps" in text
    # last Push: 82.5 x 6 on every set, range 5-8, weakest 6 -> same weight, 7 reps
    assert "82.5 kg x 7" in text
    assert "warm-up" in text.lower() and "41.25" not in text  # ramp weights are rounded to 2.5 kg plates
    assert "about 60 minutes" in text  # the last Push sessions took an hour
    assert "double progression" in " ".join(brief.rules)
    assert "Bench Press" in brief.facts and len(text) < 900


def test_the_warm_up_ramp_rounds_to_plates(tmp_path):
    h = hub(tmp_path)
    rotation(h)
    text = Coach(h, clock=lambda: NOW).pre_workout(title="Legs").text
    # squat 100 x 8 on all sets: top of 5-8, so add 5 kg -> 105 x 5; ramp 50/70/85% = 52.5 / 72.5 / 90
    assert "105 kg x 5" in text and "52.5 kg" in text and "72.5 kg" in text and "90 kg" in text


def test_exam_week_shortens_the_session(tmp_path):
    h = hub(tmp_path)
    rotation(h)
    h.set_profile("exam_week", "1")
    text = Coach(h, clock=lambda: NOW).pre_workout().text
    assert "exam week" in text.lower() and "70%" in text


def test_readiness_from_sleep_is_included_only_when_there_is_a_baseline(tmp_path):
    h = hub(tmp_path)
    rotation(h)
    assert "sleep" not in Coach(h, clock=lambda: NOW).pre_workout().text.lower().replace("warm-up", "")
    for i in range(1, 15):
        h.add_sleep("health_connect", f"s{i}", NOW - i * DAY - 8 * 3600, NOW - i * DAY, 480 if i % 2 else 450)
    h.add_sleep("health_connect", "last", NOW - 9 * 3600, NOW - 3 * 3600, 330)
    text = Coach(h, clock=lambda: NOW).pre_workout().text
    assert "short on sleep" in text and "not a health score" in text


def test_with_no_workouts_it_says_so(tmp_path):
    h = hub(tmp_path)
    brief = Coach(h, clock=lambda: NOW).pre_workout()
    assert "no workouts" in brief.text.lower() and brief.facts == ""


def test_the_debrief_names_prs_volume_and_the_next_targets(tmp_path):
    h = hub(tmp_path)
    rotation(h)
    # a new Legs session just synced: a squat PR (110 x 5 = e1RM 128.3 vs the earlier 126.7)
    add(h, "new", 0.02, "Legs", [("Squat (Barbell)", 110, 5)] * 3 + [("Leg Extension", 40, 12)] * 4)
    coach = Coach(h, clock=lambda: NOW)
    pending = coach.pending_debriefs()
    assert len(pending) == 1
    d = pending[0]
    assert "PR" in d.text and "Squat (Barbell)" in d.text and "128.3" in d.text
    assert "quads" in d.text and "7 hard sets" in d.text
    assert "Next time" in d.text and d.workout_id > 0 and len(d.text) < 900
    assert coach.pending_debriefs() == []  # once


def test_old_workouts_that_arrive_late_are_marked_seen_without_a_debrief(tmp_path):
    h = hub(tmp_path)
    rotation(h)
    coach = Coach(h, clock=lambda: NOW)
    assert coach.pending_debriefs() == []  # the whole history is "old": no flood of debriefs
    add(h, "fresh", 0.05, "Push", [("Bench Press (Barbell)", 82.5, 6)] * 3)
    assert len(coach.pending_debriefs()) == 1


def test_feedback_is_saved_per_workout(tmp_path):
    h = hub(tmp_path)
    rotation(h)
    wid = h.rows("workouts")[-1]["id"]
    h.save_feedback(wid, "effort", "hard")
    h.save_feedback(wid, "effort", "right")  # the later tap wins
    h.save_feedback(wid, "soreness", "some")
    assert h.feedback(wid) == {"effort": "right", "soreness": "some"}
    with pytest.raises(ValueError):
        h.save_feedback(wid, "mood", "ok")


def test_a_scheduled_brief_comes_once_a_day_after_the_time_and_not_on_a_training_day(tmp_path):
    from datetime import datetime

    h = hub(tmp_path)
    rotation(h)
    coach = Coach(h, clock=lambda: NOW)
    morning = datetime.fromtimestamp(NOW).replace(hour=7, minute=0)
    assert coach.scheduled_brief(morning, "18:00") is None  # not yet time
    evening = morning.replace(hour=18, minute=5)
    first = coach.scheduled_brief(evening, "18:00")
    assert first is not None and "Push" in first.text
    assert coach.scheduled_brief(evening.replace(minute=30), "18:00") is None  # once a day
    assert coach.scheduled_brief(evening, "") is None  # no time set: only on request
    h2 = hub(tmp_path / "t")
    rotation(h2)
    add(h2, "today", 0.0, "Push", [("Bench Press (Barbell)", 82.5, 6)] * 3)
    midnight_ts = datetime.fromtimestamp(NOW).replace(hour=0, minute=0, second=0).timestamp()
    h2._db.execute("UPDATE workouts SET started=? WHERE ext_id='today'", (midnight_ts + 3600,))
    assert Coach(h2, clock=lambda: NOW).scheduled_brief(evening, "18:00") is None  # you already trained today


def test_the_debrief_names_a_milestone_and_the_brief_suggests_a_lighter_week_when_lifts_have_stalled(tmp_path):
    h = hub(tmp_path)
    # squat climbs past 120 kg estimated max this month (116.7 -> 128.3); bench and row sit flat for six weeks
    for d, w in ((40, 100), (12, 110)):
        add(h, f"sq{d}", d, "Legs", [("Squat (Barbell)", w, 5)] * 3)
    for d in (41, 34, 27, 20, 13, 6):
        add(h, f"b{d}", d, "Push", [("Bench Press (Barbell)", 80, 5)] * 3)
        add(h, f"r{d}", d - 0.5, "Pull", [("Bent Over Row", 60, 5)] * 3)
    add(h, "new", 0.02, "Legs", [("Squat (Barbell)", 112.5, 5)] * 3)
    coach = Coach(h, clock=lambda: NOW)
    assert "Milestone: Squat (Barbell): passed 120 kg" in coach.pending_debriefs()[0].text
    off = Coach(h, clock=lambda: NOW, planning_on=lambda: False).pre_workout(title="Push").text
    on = Coach(h, clock=lambda: NOW).pre_workout(title="Push").text
    assert "lighter week" in on and "lighter week" not in off
