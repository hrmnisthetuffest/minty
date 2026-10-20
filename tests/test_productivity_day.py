"""673 smart alarm, 593 daily plan, 594 wind-down, 598 end-of-day summary."""
from datetime import date, datetime, timedelta

import pytest

from minty_core.productivity.alarm import Alarms, alarm_brief, ramp
from minty_core.productivity.daily import DayData, daily_plan, end_of_day, wind_down
from minty_core.productivity.db import LifeDB

THU = datetime(2026, 10, 15, 6, 30)


@pytest.fixture
def alarms(tmp_path):
    return Alarms(LifeDB(tmp_path / "life.db"))


def test_an_alarm_rings_once_a_day_at_its_time_on_its_days(alarms):
    alarms.add("06:30", days="weekdays", label="School")
    assert alarms.due(THU - timedelta(minutes=1)) == []
    (a,) = alarms.due(THU)
    assert a["label"] == "School"
    assert alarms.due(THU + timedelta(minutes=1)) == []  # not again today
    assert [x["label"] for x in alarms.due(THU + timedelta(days=1))] == ["School"]  # tomorrow
    assert alarms.due(datetime(2026, 10, 17, 6, 30)) == []  # Saturday


def test_a_missed_alarm_is_not_rung_hours_late(alarms):
    alarms.add("06:30")
    assert alarms.due(THU + timedelta(hours=3)) == []


def test_snooze_rings_again_later_and_stop_ends_it(alarms):
    alarms.add("06:30")
    (a,) = alarms.due(THU)
    alarms.start_ring(a["id"])
    alarms.snooze(THU, minutes=8)
    assert alarms.due(THU + timedelta(minutes=7)) == []
    (again,) = alarms.due(THU + timedelta(minutes=8))
    assert again["id"] == a["id"]
    alarms.start_ring(a["id"])
    assert alarms.stop() == [a["id"]]
    assert alarms.due(THU + timedelta(minutes=30)) == []


def test_a_one_time_alarm_switches_itself_off(alarms):
    alarms.add("06:30", once=True)
    assert len(alarms.due(THU)) == 1
    assert alarms.due(THU + timedelta(days=1)) == []
    assert alarms.list()[0]["enabled"] is False


def test_alarms_are_listed_and_can_be_removed_and_bad_times_refused(alarms):
    a = alarms.add("07:15", days="mon,wed", label="Gym")
    assert alarms.list()[0]["days"] == "mon,wed" and alarms.remove(a) is True and alarms.list() == []
    for bad in ("25:00", "6.30", "noon"):
        with pytest.raises(ValueError):
            alarms.add(bad)
    with pytest.raises(ValueError):
        alarms.add("07:00", days="someday")


def test_the_volume_ramps_up_gently_to_full():
    r = ramp(5)
    assert r[0] <= 0.3 and r[-1] == 1.0 and r == sorted(r) and len(r) == 5


def test_the_wake_up_brief_is_short_and_only_says_what_exists():
    text = alarm_brief(THU, first_class="Maths at 08:00", due=["Chemistry worksheet"], weather="Sunny, 24 degrees",
                       occasions=["Sam's birthday is tomorrow"])
    assert text.startswith("Good morning. It's 6:30.")
    assert "First class: Maths at 08:00." in text and "Due today: Chemistry worksheet." in text
    assert "Sunny, 24 degrees" in text and "Sam's birthday is tomorrow" in text
    assert alarm_brief(THU) == "Good morning. It's 6:30."


# ---- the day ---------------------------------------------------------------------------------------------
DAY = date(2026, 10, 15)


def data(**kw) -> DayData:
    return DayData(**kw)


def test_the_daily_plan_orders_the_day_and_skips_empty_sections():
    text = daily_plan(DAY, data(classes=["08:00 Maths", "09:30 Physics"], study_blocks=["16:30-17:10 Physics: Lab report"],
                                tasks_due=["call the dentist"], chores=["Water the plants"],
                                habits_open=["Read 20 pages (streak 4)"], occasions=["Sam's birthday in 3 days"],
                                exams=["Maths in 6 days"]))
    lines = text.splitlines()
    assert lines[0] == "Your plan for Thursday 15 Oct:"
    order = [l.split(":")[0] for l in lines[1:]]
    assert order == ["Classes", "Study", "To do", "Chores", "Habits", "Coming up"]
    assert "Sam's birthday in 3 days" in text and "Maths in 6 days" in text


def test_an_empty_day_says_so():
    assert daily_plan(DAY, data()) == "Nothing is planned for Thursday 15 Oct. A free day."


def test_wind_down_lists_the_steps_tomorrows_bag_and_the_alarm():
    text = wind_down(["Phone on charge", "Pack the bag", "Lights out by 22:30"],
                     bag="Tomorrow: Maths (geometry box). Pack: geometry box.", alarm="Your alarm is at 06:30.")
    assert text.splitlines()[0] == "Time to wind down."
    assert "1. Phone on charge" in text and "3. Lights out by 22:30" in text
    assert "Tomorrow: Maths (geometry box)." in text and "Your alarm is at 06:30." in text


def test_the_end_of_day_summary_counts_what_got_done_and_names_tomorrows_first_thing():
    text = end_of_day(DAY, data(tasks_done=3, chores_done=1, habits_done=["Read 20 pages"], cards_reviewed=14,
                                notes_saved=2, changes=5, first_tomorrow="Maths at 08:00"))
    assert text.startswith("Today: 3 tasks done, 1 chore ticked off, 1 habit (Read 20 pages), 14 flashcards, "
                           "2 notes saved.")
    assert "Tomorrow starts with Maths at 08:00." in text
    assert end_of_day(DAY, data()) == "Today was quiet: nothing was ticked off."
