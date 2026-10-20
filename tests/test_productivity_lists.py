"""595 trip planner and packing lists, 596 meal plan and shopping list, 589 to-do inbox triage."""
from datetime import date, datetime, timedelta

import pytest

from minty_core.butler.domain import Butler
from minty_core.butler.domains import all_domains
from minty_core.butler.store import ButlerStore
from minty_core.productivity.db import LifeDB
from minty_core.productivity.meals import Meals
from minty_core.productivity.todo import apply_suggestions, triage
from minty_core.productivity.trips import Trips

TODAY = date(2026, 10, 15)


@pytest.fixture
def db(tmp_path):
    return LifeDB(tmp_path / "life.db")


# ---- 595 -------------------------------------------------------------------------------------------------
def test_a_trip_gets_a_packing_list_scaled_by_days_and_by_kind(db):
    t = Trips(db)
    tid = t.create("Manali", "hills", date(2026, 11, 20), 4)
    items = [i["item"] for i in t.items("manali")]
    assert "Phone and charger" in items and "Warm jacket" in items and "4 tops" in items
    assert "Swimwear" not in items
    long = t.create("Goa", "beach", date(2026, 12, 1), 12)
    names = [i["item"] for i in t.items("goa")]
    assert "Swimwear" in names and "7 tops" in names  # clothes are capped at a week: you can wash


def test_checking_off_and_what_is_left(db):
    t = Trips(db)
    t.create("Manali", "hills", None, 3)
    first = t.items("manali")[0]["item"]
    t.check("manali", first.lower()[:8])
    left = t.remaining("manali")
    assert first not in left and len(left) == len(t.items("manali")) - 1
    t.uncheck("manali", first.lower()[:8])
    assert first in t.remaining("manali")
    t.add_item("manali", "Camera")
    assert "Camera" in t.remaining("manali")


def test_a_trip_can_be_deleted_with_its_list_and_an_unknown_kind_falls_back(db):
    t = Trips(db)
    t.create("Cousin's wedding", "wedding", None, 2)
    assert "Phone and charger" in [i["item"] for i in t.items("wedding")]
    assert t.delete("wedding") is True and t.list() == []
    with db.lock:
        assert db.db.execute("SELECT COUNT(*) FROM packing").fetchone()[0] == 0


def test_trips_are_listed_soonest_first(db):
    t = Trips(db)
    t.create("Later", "city", date(2026, 12, 1), 2)
    t.create("Sooner", "city", date(2026, 10, 20), 2)
    assert [x["name"] for x in t.list()] == ["Sooner", "Later"]


# ---- 596 -------------------------------------------------------------------------------------------------
def test_a_week_plan_turns_into_one_shopping_list_without_repeats(db):
    m = Meals(db)
    m.plan(date(2026, 10, 15), "dinner", "Dal chawal")
    m.plan(date(2026, 10, 16), "dinner", "Rajma chawal")
    m.plan(date(2026, 10, 17), "lunch", "Paneer sandwich", ["bread", "paneer", "Onion"])
    added = m.shopping_from_plan(date(2026, 10, 15), 7)
    items = [i["item"] for i in m.shopping()]
    assert items.count("rice") == 1 and "dal" in items and "rajma" in items and "paneer" in items
    assert "onion" in items and len(added) == len(items)
    assert m.shopping_from_plan(date(2026, 10, 15), 7) == []  # nothing new the second time


def test_the_shopping_list_can_be_edited_and_cleared_of_what_is_bought(db):
    m = Meals(db)
    m.add_item("Milk", "2 packets")
    m.add_item("eggs")
    m.check_item("milk")
    assert [i["item"] for i in m.shopping(done=False)] == ["eggs"]
    assert m.clear_done() == 1 and [i["item"] for i in m.shopping()] == ["eggs"]
    with pytest.raises(ValueError):
        m.add_item("  ")


def test_a_plan_slot_is_replaced_not_duplicated_and_can_be_cleared(db):
    m = Meals(db)
    m.plan(TODAY, "dinner", "Pasta")
    m.plan(TODAY, "dinner", "Khichdi")
    assert m.week(TODAY)[TODAY.isoformat()] == {"dinner": "Khichdi"}
    assert m.clear(TODAY, "dinner") is True and m.week(TODAY) == {}
    with pytest.raises(ValueError):
        m.plan(TODAY, "midnight", "x")


# ---- 589 -------------------------------------------------------------------------------------------------
@pytest.fixture
def butler(tmp_path):
    t = {"v": datetime(2026, 10, 15, 10, 0).timestamp()}
    return Butler(ButlerStore(tmp_path / "b.db", clock=lambda: t["v"]), all_domains(), clock=lambda: t["v"])


def test_undated_tasks_get_a_suggested_day_and_nothing_is_changed_until_applied(butler):
    for name in ("submit the science form", "buy a ribbon cable", "tidy my desk", "call the dentist tomorrow"):
        butler.add("tasks", name)
    butler.add("tasks", "already dated", when="friday")
    s = triage(butler, TODAY)
    by = {x.name: x for x in s}
    assert set(by) == {"submit the science form", "buy a ribbon cable", "tidy my desk", "call the dentist tomorrow"}
    assert by["submit the science form"].day == TODAY + timedelta(days=2)
    assert by["buy a ribbon cable"].day == TODAY + timedelta(days=3)
    assert by["call the dentist tomorrow"].day == TODAY + timedelta(days=1)
    assert by["tidy my desk"].day.weekday() == 5  # a weekend job
    assert all(i.due is None for i in butler.list("tasks") if i.name != "already dated")
    n = apply_suggestions(butler, s, [by["buy a ribbon cable"].item_id])
    assert n == 1
    assert [i.due is not None for i in butler.list("tasks") if i.name == "buy a ribbon cable"] == [True]
    assert [i.due is None for i in butler.list("tasks") if i.name == "tidy my desk"] == [True]
