"""576 homework planner, 577 exam countdown and revision, 943 deadline pacing, 726 timetable-aware reminders."""
from datetime import date, datetime, time, timedelta

import pytest

from minty_core.config import Config
from minty_core.study.plan import (Planner, Task, class_reminders, countdown, pace, revision_plan, tomorrow_bag)

MON = date(2026, 10, 12)  # a Monday


@pytest.fixture
def planner():
    cfg = Config()
    cfg.study.busy = {"tue": ["17:00-18:00"]}  # tuition
    return Planner(cfg)


def overlaps(a, b) -> bool:
    return a.day == b.day and a.start < b.end and b.start < a.end


def test_a_week_of_homework_never_overlaps_classes_the_night_or_itself(planner):
    tasks = [Task("m1", "Maths", "Exercise 4.2", 45, MON + timedelta(days=2)),
             Task("p1", "Physics", "Lab report", 90, MON + timedelta(days=4)),
             Task("e1", "English", "Essay outline", 40, MON + timedelta(days=3)),
             Task("c1", "Chemistry", "Worksheet", 30, MON + timedelta(days=1)),
             Task("h1", "History", "Read ch. 7", 60, MON + timedelta(days=6))]
    plan = planner.plan(tasks, today=MON, now=datetime.combine(MON, time(15, 40)))
    assert plan.unplaced == []
    assert sum(b.minutes for b in plan.blocks) == 45 + 90 + 40 + 30 + 60
    for b in plan.blocks:
        assert b.day < next(t.due for t in tasks if t.key == b.task)  # done before the due day
        if planner.is_weekday(b.day):  # a weekday: inside the study window
            assert b.start >= time(16, 30) and b.end <= time(20, 30)
        assert b.start >= time(7, 0) and b.end <= time(22, 0)  # nowhere near the night window
        assert (b.end.hour * 60 + b.end.minute) - (b.start.hour * 60 + b.start.minute) == b.minutes
    for i, a in enumerate(plan.blocks):
        for b in plan.blocks[i + 1:]:
            assert not overlaps(a, b)
    tuesday = [b for b in plan.blocks if b.day == MON + timedelta(days=1)]
    assert all(not (b.start < time(18, 0) and b.end > time(17, 0)) for b in tuesday)  # tuition


def test_earliest_deadline_goes_first_and_a_day_is_not_overloaded(planner):
    tasks = [Task("a", "Maths", "A", 120, MON + timedelta(days=5)), Task("b", "Physics", "B", 60, MON + timedelta(days=2))]
    plan = planner.plan(tasks, today=MON, now=datetime.combine(MON, time(8, 0)))
    assert plan.blocks[0].task == "b"
    per_day: dict = {}
    for b in plan.blocks:
        per_day[b.day] = per_day.get(b.day, 0) + b.minutes
    assert max(per_day.values()) <= 150


def test_what_cannot_fit_before_its_deadline_is_reported_not_squeezed(planner):
    tasks = [Task("big", "Maths", "Project", 600, MON + timedelta(days=1))]
    plan = planner.plan(tasks, today=MON, now=datetime.combine(MON, time(8, 0)))
    assert [t.key for t in plan.unplaced] == ["big"] and sum(b.minutes for b in plan.blocks) < 600
    assert "does not fit" in plan.text().lower()


def test_today_starts_from_now_and_a_past_due_item_is_flagged(planner):
    tasks = [Task("late", "Maths", "Old", 30, MON)]
    plan = planner.plan(tasks, today=MON, now=datetime.combine(MON, time(21, 0)))
    assert [t.key for t in plan.unplaced] == ["late"]


def test_weekends_use_the_weekend_window(planner):
    sat = MON + timedelta(days=5)
    plan = planner.plan([Task("w", "Maths", "W", 60, sat + timedelta(days=2))], today=sat,
                        now=datetime.combine(sat, time(8, 0)))
    assert plan.blocks[0].day == sat and plan.blocks[0].start == time(10, 0)


# ---- 577 -------------------------------------------------------------------------------------------------
def test_countdown_lists_exams_soonest_first_with_days_left():
    ex = [("Physics", date(2026, 11, 2), ["optics"]), ("Maths", date(2026, 10, 20), ["algebra", "geometry"])]
    lines = countdown(ex, MON)
    assert lines[0].startswith("Maths in 8 days (Tuesday 20 Oct)") and lines[1].startswith("Physics in 21 days")
    assert countdown([("Maths", MON, [])], MON)[0].startswith("Maths is today")
    assert countdown([("Maths", MON - timedelta(days=1), [])], MON) == []


def test_revision_covers_every_topic_twice_and_ends_with_a_full_review():
    topics = ["algebra", "geometry", "trigonometry", "statistics"]
    exam = MON + timedelta(days=9)
    plan = revision_plan("Maths", exam, topics, MON, minutes=40)
    assert [d for d, _t, _m in plan] == [MON + timedelta(days=i) for i in range(9)]
    seen = [t for _d, ts, _m in plan[:-1] for t in ts]
    assert all(seen.count(t) >= 2 for t in topics)
    assert plan[-1][1] == ["quick review of everything"] and all(m == 40 for _d, _t, m in plan)
    assert revision_plan("Maths", MON, topics, MON, 40) == []  # exam today: nothing left to plan


def test_a_short_runway_still_gets_a_plan_with_every_topic_once():
    plan = revision_plan("Maths", MON + timedelta(days=3), ["a", "b", "c", "d", "e"], MON, 30)
    seen = {t for _d, ts, _m in plan for t in ts}
    assert {"a", "b", "c", "d", "e"} <= seen


# ---- 943 -------------------------------------------------------------------------------------------------
def test_pacing_schedules_chunks_backwards_from_the_due_day(planner):
    due = MON + timedelta(days=5)  # Saturday
    blocks = pace(planner, "Science report", 180, due, MON)
    days = [b.day for b in blocks]
    assert sum(b.minutes for b in blocks) == 180 and max(b.minutes for b in blocks) <= 40
    assert max(days) == due - timedelta(days=1) and days == sorted(days)
    assert len(set(days)) >= 3  # spread out, not crammed into one evening


def test_pacing_flags_a_deadline_too_close(planner):
    blocks = pace(planner, "Report", 600, MON + timedelta(days=1), MON)
    assert sum(b.minutes for b in blocks) < 600


# ---- 726 -------------------------------------------------------------------------------------------------
TT = {"mon": [{"start": "08:00", "end": "08:40", "subject": "Maths", "bring": "geometry box"},
              {"start": "09:30", "end": "10:10", "subject": "Physics", "bring": "lab coat"}],
      "tue": [{"start": "08:00", "end": "08:40", "subject": "English", "bring": ""}]}


def test_a_class_reminder_fires_only_inside_the_lead_time():
    assert class_reminders(TT, datetime(2026, 10, 12, 7, 40), 15) == []
    (r,) = class_reminders(TT, datetime(2026, 10, 12, 7, 50), 15)
    assert r == "Maths at 08:00. Bring: geometry box."
    assert class_reminders(TT, datetime(2026, 10, 12, 8, 5), 15) == []  # already started
    assert class_reminders(TT, datetime(2026, 10, 17, 7, 50), 15) == []  # Saturday: no timetable


def test_tomorrows_bag_lists_what_to_pack():
    assert tomorrow_bag(TT, MON) == "Tomorrow: English. Nothing special to pack."
    assert tomorrow_bag(TT, MON - timedelta(days=1)) == ("Tomorrow: Maths (geometry box), Physics (lab coat). "
                                                         "Pack: geometry box, lab coat.")
    assert tomorrow_bag({}, MON) == ""


# ---- 731 -------------------------------------------------------------------------------------------------
def test_competitions_come_from_the_butler_framework_with_early_leads(tmp_path):
    from minty_core.butler.domain import Butler
    from minty_core.butler.domains import all_domains
    from minty_core.butler.store import ButlerStore

    t = {"v": datetime(2026, 10, 1, 10, 0).timestamp()}
    b = Butler(ButlerStore(tmp_path / "b.db", clock=lambda: t["v"]), all_domains(), clock=lambda: t["v"])
    out = b.capture("the maths olympiad registration closes on 20 Oct")
    assert "olympiad" in out.lower() and "20 Oct" in out
    assert b.notices(t["v"]) == []  # 19 days away
    t["v"] += 6 * 86400  # 13 days left: the 14-day lead
    (n,) = b.notices(t["v"])
    assert n.lead == 14 and "13 days" in n.text
