"""Times are shown and spoken on a 12-hour clock with AM and PM."""
from datetime import datetime

import pytest

from minty_core.timefmt import clock12  # noqa: E402


@pytest.mark.parametrize("hour, minute, expected", [
    (0, 0, "12:00 AM"), (0, 5, "12:05 AM"), (9, 30, "9:30 AM"), (11, 59, "11:59 AM"),
    (12, 0, "12:00 PM"), (12, 45, "12:45 PM"), (13, 7, "1:07 PM"), (22, 47, "10:47 PM"), (23, 59, "11:59 PM"),
])
def test_clock12(hour, minute, expected):
    assert clock12(datetime(2026, 10, 20, hour, minute)) == expected


def test_a_timestamp_works_too():
    assert clock12(datetime(2026, 10, 20, 15, 10).timestamp()) == "3:10 PM"


@pytest.fixture
def restore_clock():
    from minty_core import timefmt
    yield timefmt
    timefmt.configure(False)


def test_the_setting_switches_every_time_to_24_hours(restore_clock):
    when = datetime(2026, 10, 20, 15, 10)
    assert restore_clock.clock_text(when) == "3:10 PM"
    restore_clock.configure(True)
    assert restore_clock.clock_text(when) == "15:10" and restore_clock.is_24h()
    assert restore_clock.clock_text(datetime(2026, 10, 20, 7, 5)) == "07:05"
    assert clock12(when) == "3:10 PM"  # the fixed 12-hour writer does not change


def test_the_config_store_applies_the_setting_on_load_and_on_save(tmp_path, restore_clock):
    from minty_core.config import ConfigStore
    store = ConfigStore(tmp_path)
    store.load()
    assert restore_clock.is_24h() is False
    data = store.config.model_dump(mode="json")
    data["clock_24h"] = True
    store.update(data)
    assert restore_clock.is_24h() is True
    other = ConfigStore(tmp_path)
    restore_clock.configure(False)
    other.load()
    assert restore_clock.is_24h() is True  # read back from the file at start-up


def test_the_big_clock_follows_the_setting(restore_clock):
    from minty_core.tui import clock_block, clock_text
    wall = datetime(2026, 10, 19, 22, 47)
    twelve = clock_text(wall, (255, 180, 60), True, False).plain
    assert "PM" in twelve
    restore_clock.configure(True)
    full = clock_text(wall, (255, 180, 60), True, False).plain
    assert "PM" not in full and "AM" not in full
    assert clock_block(wall) != clock_block(wall.replace(hour=10))  # 22, not 10
    assert {len(r) for r in clock_block(wall)} == {len(r) for r in clock_block(wall.replace(hour=7))}
    assert clock_block(wall.replace(hour=7))[0][:9] != " " * 9  # 07: the zero is drawn in 24-hour time
