from datetime import datetime, timedelta

import pytest

from tests.instant_fakes import IST, NOW, engine, say

E = engine()  # Tuesday 20 October 2026, 4:32 PM IST


@pytest.mark.parametrize("said, reply", [
    ("what time is it", "It's 4:32 PM."), ("Minty, what's the time?", "It's 4:32 PM."), ("time please", "It's 4:32 PM."),
    ("what's the date", "It's Tuesday, 20 October 2026."), ("what day is it", "It's Tuesday, 20 October."),
    ("what month is it", "It's October."), ("what year is it", "It's 2026."), ("what week is it", "It's week 43 of 2026."),
    ("is it the weekend", "No, it's Tuesday."), ("is it a weekday", "Yes, it's Tuesday."),
])
def test_now(said, reply):
    assert say(E, said) == reply


@pytest.mark.parametrize("hour, minute, reply", [(0, 0, "It's midnight."), (12, 0, "It's noon."), (9, 5, "It's 9:05 AM."),
                                                 (15, 0, "It's 3 PM."), (0, 30, "It's 12:30 AM."), (23, 59, "It's 11:59 PM.")])
def test_the_time_is_said_the_way_people_say_it(hour, minute, reply):
    assert say(engine(now=NOW.replace(hour=hour, minute=minute)), "what time is it") == reply


@pytest.mark.parametrize("said, reply", [
    ("what day is tomorrow", "Tomorrow is Wednesday, 21 October."), ("what was yesterday", "Yesterday was Monday, 19 October."),
    ("what's the date the day after tomorrow", "The day after tomorrow is Thursday, 22 October."),
    ("what day is it in 10 days", "In 10 days it will be Friday, 30 October."),
    ("what day was it 3 days ago", "3 days ago it was Saturday, 17 October."),
    ("what date is it in a month", None),
    ("what date is it in 2 months", "In 2 months it will be Sunday, 20 December."),
    ("when is next friday", "Next Friday is 23 October."), ("when is tuesday", "That's today, Tuesday, 20 October."),
    ("what date is next tuesday", "Next Tuesday is 27 October."), ("when is last monday", "Last Monday was Monday, 19 October."),
    ("when is christmas", "Christmas is on Friday, 25 December."), ("what day is halloween", "Halloween is on Saturday, 31 October."),
    ("when is new year's day", "New Year's Day is on Friday, 1 January 2027."),
])
def test_other_days(said, reply):
    assert say(E, said) == reply


@pytest.mark.parametrize("said, reply", [
    ("how many days until christmas", "66 days until Christmas."), ("how many days till halloween", "11 days until Halloween."),
    ("how many days until friday", "3 days until Friday."), ("how many days until the weekend", "4 days until the weekend."),
    ("how many days until 15 august", "299 days until 15 August."), ("how many days until december 25 2027", "431 days until 25 December 2027."),
    ("how many days left in the year", "72 days left in 2026."), ("how many days left this month", "11 days left in October."),
    ("how many days since 1 january 2026", "292 days since 1 January 2026."), ("how many days since 1 january", None),
    ("how many days until tuesday", "Tuesday is today."), ("how many days until 21 october", "21 October is tomorrow."),
    ("how many weeks until christmas", "Christmas is in 9.4 weeks, which is 66 days."),
])
def test_counting_days(said, reply):
    assert say(E, said) == reply


def test_it_is_already_the_weekend():
    saturday = NOW + timedelta(days=4)
    assert say(engine(now=saturday), "how many days until the weekend") == "It's the weekend now."
    assert say(engine(now=saturday), "is it the weekend") == "Yes, it's Saturday."


@pytest.mark.parametrize("said, reply", [
    ("what time will it be in 2 hours", "In 2 hours it will be 6:32 PM."),
    ("what time will it be in half an hour", "In 30 minutes it will be 5:02 PM."),
    ("what time was it 3 hours ago", "3 hours ago it was 1:32 PM."),
    ("how long until 6 pm", "1 hour 28 minutes until 6 PM."), ("how long until 18:00", "1 hour 28 minutes until 6 PM."),
    ("how many minutes until noon", "19 hours 28 minutes until noon."), ("how long till midnight", "7 hours 28 minutes until midnight."),
    ("how many hours until 5", "28 minutes until 5 PM."),
])
def test_clock_maths(said, reply):
    assert say(E, said) == reply


@pytest.mark.parametrize("said, reply", [
    ("what time is it in tokyo", "It's 8:02 PM in Tokyo."), ("time in london", "It's 12:02 PM in London."),
    ("what's the time in new york", "It's 7:02 AM in New York."), ("dubai time", "It's 3:02 PM in Dubai."),
    ("what time is it in india", "It's 4:32 PM in India."), ("what time is it in utc", "It's 11:02 AM in UTC."),
    ("what is my time zone", "Your time zone is IST (UTC+05:30)."),
])
def test_other_places(said, reply):
    assert say(E, said) == reply


def test_a_place_on_the_other_side_of_the_date_line_says_which_day():
    late = engine(now=datetime(2026, 10, 20, 23, 30, tzinfo=IST))
    assert say(late, "what time is it in tokyo") == "It's 3 AM tomorrow in Tokyo."
    early = engine(now=datetime(2026, 10, 20, 1, 0, tzinfo=IST))
    assert say(early, "what time is it in new york") == "It's 3:30 PM yesterday in New York."


@pytest.mark.parametrize("said", ["what time is it in narnia", "bed time", "dinner time", "what time is it in the morning"])
def test_unknown_places_go_to_the_model(said):
    assert say(E, said) is None


@pytest.mark.parametrize("said, reply", [
    ("is 2028 a leap year", "Yes, 2028 is a leap year."), ("is 2100 a leap year", "No, 2100 is not a leap year."),
    ("is 2000 a leap year", "Yes, 2000 is a leap year."), ("is this a leap year", "No, 2026 is not a leap year."),
    ("how many days in february", "February has 28 days, or 29 in a leap year."),
    ("how many days in february 2028", "February 2028 has 29 days."), ("how many days in april", "April 2026 has 30 days."),
    ("how many days in next month", "November 2026 has 30 days."), ("how many days in a year", "A year has 365 days, or 366 in a leap year."),
])
def test_calendar_facts(said, reply):
    assert say(E, said) == reply


def test_the_clock_follows_the_system_clock_when_not_faked():
    from minty_core.instant import Env, Instant

    reply = Instant(Env()).answer("what year is it").reply
    assert reply == f"It's {datetime.now().year}."
