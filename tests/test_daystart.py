"""Day-start routine: once, on the first conversation after 04:00 (W1B-09)."""
import asyncio
from datetime import datetime

from minty_core.daystart import DayStart


def make(tmp_path, clock, pending=(), enabled=True, said=None):
    said = said if said is not None else []

    async def compose():
        return "Good morning. It's Wednesday. Sunny, 31 degrees."

    ds = DayStart(tmp_path / "daystart", compose=compose, notify=said.append, pending_approvals=lambda: list(pending),
                  enabled=lambda: enabled, clock=lambda: clock["now"])
    return ds, said


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def test_the_first_conversation_of_the_day_starts_it_once(tmp_path):
    clock = {"now": datetime(2026, 10, 15, 5, 40)}
    ds, said = make(tmp_path, clock, pending=[object(), object()])
    ds.on_first()
    ds.on_first()  # a second conversation straight after
    await settle()
    assert said == ["Good morning. It's Wednesday. Sunny, 31 degrees. 2 things are waiting for your approval."]
    clock["now"] = datetime(2026, 10, 16, 6, 0)  # tomorrow
    ds.on_first()
    await settle()
    assert len(said) == 2


async def test_before_four_in_the_morning_or_when_off_it_stays_quiet(tmp_path):
    clock = {"now": datetime(2026, 10, 15, 3, 30)}
    ds, said = make(tmp_path, clock)
    ds.on_first()
    await settle()
    assert said == [] and not ds.due()
    off, said2 = make(tmp_path / "x", {"now": datetime(2026, 10, 15, 9, 0)}, enabled=False)
    off.on_first()
    await settle()
    assert said2 == []


async def test_a_brief_already_given_today_counts(tmp_path):
    clock = {"now": datetime(2026, 10, 15, 7, 30)}
    ds, said = make(tmp_path, clock)
    ds.mark_done(clock["now"].date())  # the scheduled morning brief ran at 7:00
    ds.on_first()
    await settle()
    assert said == []


async def test_a_failing_brief_is_harmless(tmp_path):
    async def broken():
        raise RuntimeError("weather is down")

    said = []
    ds = DayStart(tmp_path / "s", compose=broken, notify=said.append, clock=lambda: datetime(2026, 10, 15, 9, 0))
    ds.on_first()
    await settle()
    assert said == []
