"""Training planning and review (WP-28): programme, deload, PR board, streaks, balance, minutes, goal, rescue."""
import random

import pytest

from minty_core.coach.engine import SetRec, Session
from minty_core.coach.planning import (active_minutes, balance_map, build_programme, consistency, deload_plan,
                                       goal_plan, pr_board, rescue_plan, weekly_sets_in_programme)

DAY = 86400.0
WEEK = 7 * DAY
NOW = 1_800_000_000.0


def S(i, days_ago, title, sets, dur=3600):
    return Session(i, NOW - days_ago * DAY, title, dur, "hevy", [SetRec(*s) for s in sets])


def lift(ex, w, r, n=3):
    return [(ex, i, "normal", w, r, None) for i in range(n)]


def test_a_programme_follows_the_days_and_the_equipment():
    full = build_programme(3, {"barbell", "gym"})
    assert [d.title for d in full.days] == ["Full body A", "Full body B", "Full body C"]
    ul = build_programme(4, {"gym"})
    assert [d.title for d in ul.days] == ["Upper A", "Lower A", "Upper B", "Lower B"]
    ppl = build_programme(6, {"gym"})
    assert [d.title for d in ppl.days][:3] == ["Push A", "Pull A", "Legs A"] and len(ppl.days) == 6
    home = build_programme(3, {"bodyweight"})
    names = {s.exercise for d in home.days for s in d.slots}
    assert names and all("Barbell" not in n and "Machine" not in n and "Cable" not in n for n in names)


def test_the_weekly_sets_land_in_the_band_for_the_big_muscles():
    prog = build_programme(4, {"gym"})
    sets = weekly_sets_in_programme(prog)
    for muscle in ("chest", "back", "quads"):
        assert 10 <= sets[muscle] <= 20, (muscle, sets)
    assert "copy" in prog.render().lower() and "Hevy" in prog.render()
    assert prog.to_csv().splitlines()[0].startswith("day,exercise,sets,reps")


def test_a_deload_week_lowers_load_and_sets_and_says_why():
    sessions = [S(1, 2, "Push", lift("Bench Press (Barbell)", 80, 6, 4)), S(2, 4, "Legs", lift("Squat (Barbell)", 100, 5, 4))]
    plan = deload_plan(sessions, NOW, reasons=["2 lifts stopped moving"])
    bench = next(x for x in plan.items if x.exercise == "Bench Press (Barbell)")
    assert bench.weight_kg == 72.5 and bench.sets == 3  # 90% of 80 = 72 -> plate 72.5; 60% of 4 sets = 2.4 -> 3
    assert "2 lifts stopped moving" in plan.text and "lighter" in plan.text


def test_the_pr_board_and_milestones():
    hist = [S(1, 60, "Legs", lift("Squat (Barbell)", 100, 5)), S(2, 20, "Legs", lift("Squat (Barbell)", 110, 5)),
            S(3, 5, "Legs", lift("Squat (Barbell)", 112.5, 6)), S(4, 3, "Push", lift("Bench Press (Barbell)", 60, 8))]
    board = pr_board(hist, NOW)
    squat = next(b for b in board.lifts if b.exercise == "Squat (Barbell)")
    assert squat.e1rm == pytest.approx(135.0)  # 112.5 * (1 + 6/30)
    assert any("Squat (Barbell)" in m and "120" in m for m in board.milestones)  # crossed 120 kg e1RM in the last 30 days
    assert not any("Bench" in m for m in board.milestones)


def test_consistency_streaks_count_whole_weeks_that_met_the_target():
    # target 3 a week: 4 weeks ago 3 sessions, 3 weeks ago 2 (breaks), then 3, 3, 3 -> current 3 weeks, longest 3
    days = [27, 26, 24, 20, 19, 13, 12, 10, 6, 5, 4, 1, 0.5, 0.2]
    # build the week structure from "weeks ago": week index = floor(days / 7) counted back from this week
    sessions = [S(i, d, "Any", []) for i, d in enumerate(days)]
    c = consistency(sessions, NOW, per_week=3)
    assert c.current_streak_weeks >= 2 and c.longest_streak_weeks >= c.current_streak_weeks
    assert 0 <= c.score_pct <= 100
    empty = consistency([], NOW, per_week=3)
    assert (empty.current_streak_weeks, empty.longest_streak_weeks, empty.score_pct) == (0, 0, 0)


def test_the_balance_map_flags_a_push_heavy_month():
    hist = []
    for i in range(8):
        hist.append(S(i, 3 * i + 1, "Push", lift("Bench Press (Barbell)", 60, 8, 5) + lift("Overhead Press", 40, 8, 4)))
    hist.append(S(99, 10, "Pull", lift("Bent Over Row", 50, 8, 3)))
    b = balance_map(hist, NOW, days=28)
    assert b.push_pull_ratio > 1.5 and any("pull" in f.lower() for f in b.flags)
    even = balance_map([S(1, 2, "Push", lift("Bench Press (Barbell)", 60, 8, 4)),
                        S(2, 3, "Pull", lift("Bent Over Row", 60, 8, 4))], NOW, days=28)
    assert even.flags == []


def test_active_minutes_add_workouts_and_activities_against_the_guideline():
    wk = [S(1, 1, "A", [], dur=3600), S(2, 3, "B", [], dur=2700), S(3, 9, "Old", [], dur=3600)]
    acts = [(NOW - 2 * DAY, 1800.0), (NOW - 20 * DAY, 3600.0)]
    m = active_minutes(wk, acts, NOW, days=7)
    assert m.minutes == 60 + 45 + 30 and m.pct_of_guideline == pytest.approx(135 / 150 * 100, abs=0.1)


def rising(rate_per_week, weeks=10, start=100.0):
    out = []
    for w in range(weeks):
        e = start + rate_per_week * w
        weight = e / (1 + 5 / 30)
        out.append(S(w, (weeks - 1 - w) * 7, "Legs", lift("Squat (Barbell)", round(weight, 2), 5)))
    return out


def test_the_reverse_planner_says_when_a_goal_is_not_realistic():
    hist = rising(0.5)  # +0.5 kg a week
    current = 100 + 0.5 * 9
    plan = goal_plan(hist, NOW, "Squat (Barbell)", target_e1rm=current + 30, weeks_left=6)
    assert plan.verdict == "not realistic" and "not realistic" in plan.text
    assert plan.required_per_week == pytest.approx(5.0, abs=0.05) and plan.observed_per_week == pytest.approx(0.5, abs=0.05)
    ok = goal_plan(hist, NOW, "Squat (Barbell)", target_e1rm=current + 2.5, weeks_left=6)
    assert ok.verdict == "realistic" and "range" in ok.text
    thin = goal_plan(hist[:2], NOW, "Squat (Barbell)", target_e1rm=150, weeks_left=6)
    assert thin.verdict == "unknown" and "data" in thin.text


MUSCLES = {"Push": {"chest", "shoulders", "triceps"}, "Pull": {"back", "biceps"}, "Legs": {"quads", "hamstrings", "glutes"},
           "Upper": {"chest", "back", "shoulders"}, "Lower": {"quads", "hamstrings"}}


def test_a_rescued_session_never_lands_within_48_hours_of_one_that_shares_a_muscle():
    rng = random.Random(5)
    for _ in range(300):
        titles = rng.sample(list(MUSCLES), k=rng.randint(3, 5))
        slots = sorted(rng.sample(range(7), k=len(titles)))
        week = [(d, t, MUSCLES[t]) for d, t in zip(slots, titles)]
        missed = rng.randrange(len(week))
        today = rng.randint(0, 6)
        plan = rescue_plan(week, missed, today)
        if plan.day is not None:
            assert plan.day >= today
            others = [w for i, w in enumerate(week) if i != missed]
            shared = [o for o in others if o[2] & week[missed][2]]
            assert all(abs(o[0] - plan.day) >= 2 for o in shared), (week, missed, today, plan)
        else:
            assert "don't double up" in plan.text.lower() or "skip" in plan.text.lower()


def test_rescue_takes_the_first_day_that_keeps_the_gap_and_otherwise_skips():
    week = [(0, "Push", MUSCLES["Push"]), (2, "Pull", MUSCLES["Pull"]), (4, "Legs", MUSCLES["Legs"]),
            (5, "Push", MUSCLES["Push"])]
    assert rescue_plan(week, 0, today=1).day == 1  # Tuesday is free and two days clear of Saturday's Push
    assert rescue_plan(week, 0, today=3).day == 3  # Thursday: still two days from Saturday
    boxed = [(0, "Push", MUSCLES["Push"]), (2, "Push", MUSCLES["Push"]), (4, "Push", MUSCLES["Push"]),
             (6, "Push", MUSCLES["Push"])]
    plan = rescue_plan(boxed, 0, today=1)
    assert plan.day is None and "don't double up" in plan.text.lower()
