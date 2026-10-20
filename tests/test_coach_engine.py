"""The training engine: deterministic numbers the model only explains (LF-07). Expected values are worked by hand."""
import pytest

from minty_core.coach.engine import (SetRec, Session, adherence, best_e1rm, brzycki, epley, hard_sets_by_muscle,
                                     is_working, muscle_of, next_targets, plateau, prs_in, running_load,
                                     volume_status)

DAY = 86400.0
NOW = 1_800_000_000.0


def S(i, days_ago, title, sets, dur=3600):
    return Session(id=i, started=NOW - days_ago * DAY, title=title, duration_s=dur, origin="hevy", sets=sets)


def st(ex, w, reps, kind="normal", rpe=None, idx=0):
    return SetRec(exercise=ex, idx=idx, kind=kind, weight_kg=w, reps=reps, rpe=rpe)


def test_the_two_one_rep_max_formulas():
    assert epley(100, 5) == pytest.approx(116.6667, abs=1e-3)  # 100 * (1 + 5/30)
    assert epley(100, 1) == 100.0
    assert brzycki(100, 5) == pytest.approx(112.5)  # 100 * 36 / (37 - 5)
    assert epley(0, 5) == 0.0 and epley(100, 0) == 0.0


@pytest.mark.parametrize("name, muscle", [
    ("Bench Press (Barbell)", "chest"), ("Incline Dumbbell Fly", "chest"), ("Lat Pulldown (Cable)", "back"),
    ("Bent Over Row", "back"), ("Deadlift (Barbell)", "back"), ("Squat (Barbell)", "quads"),
    ("Leg Extension", "quads"), ("Leg Curl", "hamstrings"), ("Romanian Deadlift", "hamstrings"),
    ("Hip Thrust", "glutes"), ("Standing Calf Raise", "calves"), ("Overhead Press", "shoulders"),
    ("Lateral Raise", "shoulders"), ("Bicep Curl (Dumbbell)", "biceps"), ("Triceps Pushdown", "triceps"),
    ("Plank", "core"), ("Wrist Curl", "forearms"), ("Zxcv Machine", "other"),
])
def test_exercises_map_to_one_primary_muscle(name, muscle):
    assert muscle_of(name) == muscle


def test_warmups_and_empty_sets_are_not_hard_sets():
    assert is_working(st("Bench", 60, 8)) and is_working(st("Pull Up", 0, 8))
    assert not is_working(st("Bench", 40, 10, kind="warmup")) and not is_working(st("Bench", 60, 0))


def squat_history():
    # 100x5, 100x6, 105x5, 105x6, 110x5, 110x6 a week apart; worked by hand: e1RM 116.67, 120, 122.5, 126, 128.33, 132
    plan = [(35, 100, 5), (28, 100, 6), (21, 105, 5), (14, 105, 6), (7, 110, 5), (0, 110, 6)]
    return [S(i, d, "Legs", [st("Squat (Barbell)", w, r)]) for i, (d, w, r) in enumerate(plan)]


def test_best_one_rep_max_and_the_pr_in_the_newest_session():
    hist = squat_history()
    assert best_e1rm(hist, "Squat (Barbell)") == pytest.approx(132.0)
    assert best_e1rm(hist[:-1], "Squat (Barbell)") == pytest.approx(128.3333, abs=1e-3)
    prs = prs_in(hist[-1], hist[:-1])
    assert len(prs) == 1 and prs[0].exercise == "Squat (Barbell)"
    assert prs[0].e1rm == pytest.approx(132.0) and prs[0].previous == pytest.approx(128.3333, abs=1e-3)
    assert prs_in(hist[3], hist[:3]) != [] and prs_in(S(9, 0, "Legs", [st("Squat (Barbell)", 90, 5)]), hist) == []


def test_a_first_ever_log_is_not_called_a_pr():
    first = S(1, 0, "Push", [st("Bench Press (Barbell)", 60, 8)])
    assert prs_in(first, []) == []


def test_weekly_hard_sets_by_muscle_and_the_band():
    week = [S(1, 2, "Push", [st("Bench Press (Barbell)", 40, 10, kind="warmup"), st("Bench Press (Barbell)", 60, 8),
                             st("Bench Press (Barbell)", 60, 8), st("Bench Press (Barbell)", 60, 7)]),
            S(2, 4, "Pull", [st("Bent Over Row", 50, 10, idx=i) for i in range(4)]),
            S(3, 9, "Old", [st("Squat (Barbell)", 100, 5, idx=i) for i in range(5)])]  # outside the week
    counts = hard_sets_by_muscle(week, NOW, days=7)
    assert counts == {"chest": 3, "back": 4}
    assert volume_status(3) == "low" and volume_status(12) == "in range" and volume_status(22) == "high"


def test_double_progression_adds_a_rep_then_adds_load():
    last = S(1, 1, "Mixed", [st("Squat (Barbell)", 100, 6), st("Squat (Barbell)", 100, 5), st("Squat (Barbell)", 100, 5),
                             st("Bench Press (Barbell)", 80, 8), st("Bench Press (Barbell)", 80, 8),
                             st("Bench Press (Barbell)", 80, 8),
                             st("Leg Press", 160, 12), st("Leg Press", 160, 12)])
    targets = {t.exercise: t for t in next_targets([last], ["Squat (Barbell)", "Bench Press (Barbell)", "Leg Press"])}
    assert (targets["Squat (Barbell)"].weight_kg, targets["Squat (Barbell)"].reps) == (100, 6)  # min 5 reps + 1
    assert (targets["Bench Press (Barbell)"].weight_kg, targets["Bench Press (Barbell)"].reps) == (82.5, 5)  # top of 5-8
    assert (targets["Leg Press"].weight_kg, targets["Leg Press"].reps) == (165, 8)  # top of 8-12, +5 kg lower body
    assert "reps" in targets["Squat (Barbell)"].why


def test_a_climbing_lift_is_not_a_plateau_and_a_flat_one_is():
    assert plateau(squat_history(), NOW, "Squat (Barbell)") is False
    flat = [S(0, 49, "Push", [st("Bench Press (Barbell)", 82.5, 5)])]  # the best, seven weeks ago
    flat += [S(i, d, "Push", [st("Bench Press (Barbell)", 80, 5)]) for i, d in enumerate((35, 28, 21, 14, 7), 1)]
    assert plateau(flat, NOW, "Bench Press (Barbell)") is True
    assert plateau(flat[:3], NOW, "Bench Press (Barbell)") is False  # too few sessions to say


def test_adherence_against_the_weekly_target():
    sessions = [S(i, d, "Any", []) for i, d in enumerate((1, 3, 5, 8, 10, 12, 15, 17, 19, 40))]
    assert adherence(sessions, NOW, per_week=3, weeks=4) == pytest.approx(9 / 12)
    assert adherence([], NOW, per_week=3) == 0.0


def test_running_load_flags_a_big_jump_but_not_a_small_one():
    runs = [(NOW - 2 * DAY, 10_000), (NOW - 4 * DAY, 10_000), (NOW - 9 * DAY, 6_000), (NOW - 11 * DAY, 6_000)]
    big = running_load(runs, NOW)
    assert big.this_week_km == 20.0 and big.last_week_km == 12.0 and big.flag is True
    assert big.change_pct == pytest.approx(66.7, abs=0.1)
    small = running_load([(NOW - 2 * DAY, 12_000), (NOW - 9 * DAY, 11_000)], NOW)
    assert small.flag is False
    tiny = running_load([(NOW - 2 * DAY, 4_000), (NOW - 9 * DAY, 1_000)], NOW)
    assert tiny.flag is False and tiny.note  # too little last week to call a jump
