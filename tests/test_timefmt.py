"""Times are shown and spoken on a 12-hour clock with AM and PM."""
from datetime import datetime

import pytest

from minty_core.timefmt import clock12


@pytest.mark.parametrize("hour, minute, expected", [
    (0, 0, "12:00 AM"), (0, 5, "12:05 AM"), (9, 30, "9:30 AM"), (11, 59, "11:59 AM"),
    (12, 0, "12:00 PM"), (12, 45, "12:45 PM"), (13, 7, "1:07 PM"), (22, 47, "10:47 PM"), (23, 59, "11:59 PM"),
])
def test_clock12(hour, minute, expected):
    assert clock12(datetime(2026, 10, 20, hour, minute)) == expected


def test_a_timestamp_works_too():
    assert clock12(datetime(2026, 10, 20, 15, 10).timestamp()) == "3:10 PM"
