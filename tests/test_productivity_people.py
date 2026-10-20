"""590 habits with streaks, 592 occasions, 601 people notes with consent and cascade delete."""
from datetime import date, timedelta

import pytest

from minty_core.productivity.db import LifeDB
from minty_core.productivity.habits import Habits
from minty_core.productivity.people import People

TODAY = date(2026, 10, 15)


@pytest.fixture
def db(tmp_path):
    return LifeDB(tmp_path / "life.db")


def test_a_habit_counts_a_streak_of_consecutive_days_and_breaks_on_a_miss(db):
    h = Habits(db)
    h.add("Read 20 pages")
    for back in (4, 3, 2, 1, 0):
        h.check("read 20", TODAY - timedelta(days=back))
    assert h.status(TODAY)[0]["streak"] == 5 and h.status(TODAY)[0]["done_today"] is True
    h.check("read 20", TODAY - timedelta(days=7))
    s = Habits(db).status(TODAY)[0]
    assert s["streak"] == 5 and s["best"] == 5
    gap = Habits(db).status(TODAY + timedelta(days=2))[0]
    assert gap["streak"] == 0 and gap["best"] == 5  # two days missed: the streak is gone, the best is kept


def test_today_not_yet_done_does_not_break_yesterdays_streak(db):
    h = Habits(db)
    h.add("Gym")
    for back in (3, 2, 1):
        h.check("gym", TODAY - timedelta(days=back))
    s = h.status(TODAY)[0]
    assert s["streak"] == 3 and s["done_today"] is False


def test_weekday_habits_do_not_break_on_a_weekend(db):
    h = Habits(db)
    h.add("Homework first", every="weekdays")
    fri, mon = date(2026, 10, 16), date(2026, 10, 19)
    for d in (date(2026, 10, 14), date(2026, 10, 15), fri, mon):
        h.check("homework first", d)
    assert h.status(mon)[0]["streak"] == 4


def test_checking_twice_counts_once_and_unknown_habits_are_refused(db):
    h = Habits(db)
    h.add("Water")
    h.check("water", TODAY)
    h.check("water", TODAY)
    assert h.status(TODAY)[0]["streak"] == 1
    with pytest.raises(ValueError):
        h.check("nothing like that", TODAY)
    with pytest.raises(ValueError):
        h.add("  ")


def test_a_habit_can_be_removed_with_its_history(db):
    h = Habits(db)
    h.add("Water")
    h.check("water", TODAY)
    assert h.remove("water") is True
    assert h.status(TODAY) == []


# ---- people ----------------------------------------------------------------------------------------------
def test_a_person_has_facts_with_how_you_know_them_and_a_birthday(db):
    p = People(db)
    p.add("Sam Taylor", birthday="03-21")
    p.add_fact("sam", "Likes mango lassi", source="told me")
    p.add_fact("sam", "Seems nervous before exams", source="my observation")
    got = p.get("Sam")
    assert got["name"] == "Sam Taylor" and got["birthday"] == "03-21"
    assert [(f["text"], f["source"]) for f in got["facts"]] == [("Likes mango lassi", "told me"),
                                                                ("Seems nervous before exams", "my observation")]
    with pytest.raises(ValueError):
        p.add_fact("sam", "x", source="rumour")


def test_upcoming_occasions_count_down_and_wrap_the_year(db):
    p = People(db)
    p.add("Sam", birthday="10-18")
    p.add("Dad", birthday="01-02")
    p.add("Meera", birthday="10-15")
    p.add_occasion("Meera", "Anniversary", "10-30")
    out = p.upcoming(TODAY, within_days=20)
    assert [(o["who"], o["what"], o["in_days"]) for o in out] == [
        ("Meera", "Birthday", 0), ("Sam", "Birthday", 3), ("Meera", "Anniversary", 15)]
    far = p.upcoming(date(2026, 12, 30), within_days=10)
    assert [(o["who"], o["in_days"]) for o in far] == [("Dad", 3)]
    assert p.upcoming(TODAY, within_days=1)[0]["in_days"] == 0


def test_a_bad_date_is_refused(db):
    p = People(db)
    for bad in ("2026-13-45", "02-30", "tomorrow"):
        with pytest.raises(ValueError):
            p.add("Sam", birthday=bad)


def test_deleting_a_person_removes_their_facts_occasions_and_derived_items(db):
    p = People(db)
    p.add("Sam Taylor", birthday="03-21")
    p.add_fact("sam", "Likes mango lassi", source="told me")
    p.add_occasion("sam", "Graduation", "06-01")
    p.add("Dad", birthday="01-02")
    removed = []
    p.on_delete.append(lambda name: removed.append(name))  # the butler follow-ups and notes folder hook in here
    assert p.delete("sam") is True
    assert p.get("sam") is None and p.upcoming(TODAY, 365)[0]["who"] == "Dad"
    assert [o["who"] for o in p.upcoming(TODAY, 365)] == ["Dad"]
    with db.lock:
        assert db.db.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
        assert db.db.execute("SELECT COUNT(*) FROM occasions WHERE who='Sam Taylor'").fetchone()[0] == 0
    assert removed == ["Sam Taylor"] and p.delete("sam") is False


def test_people_notes_refuse_what_looks_like_an_id_or_a_secret(db):
    p = People(db)
    p.add("Sam")
    with pytest.raises(ValueError):
        p.add_fact("sam", "her phone is 98765 43210 and aadhaar 2345 6789 0124", source="told me")
    with pytest.raises(ValueError):
        p.add_fact("sam", "my password for her account is hunter2", source="told me")
