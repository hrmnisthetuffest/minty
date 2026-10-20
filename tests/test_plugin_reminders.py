from datetime import date, datetime

from tests.plugin_helpers import make_host
from minty_core.scheduler import Scheduler

MON = date(2026, 10, 12)


def build(tmp_path):
    spoken, shown = [], []
    now = [datetime(2026, 10, 12, 9, 0)]
    sched = Scheduler(tmp_path / "s.db", speak=spoken.append, show=shown.append, clock=lambda: now[0])
    host = make_host(tmp_path / "cfg", services={"scheduler": sched, "now": lambda: now[0]})
    return host, sched, spoken, now


async def test_set_list_and_cancel(tmp_path):
    host, sched, spoken, now = build(tmp_path)
    out = await host.call("reminders_set", {"text": "call the dentist", "at": "10:00"})
    assert out == "Okay, reminder 1: I'll remind you today at 10:00 to call the dentist."
    out = await host.call("reminders_set", {"text": "pack the bag", "at": "2026-10-13T07:00"})
    assert "tomorrow at 07:00" in out
    out = await host.call("reminders_set", {"text": "stretch", "in_minutes": 20})
    assert "today at 09:20" in out
    assert (await host.call("reminders_list", {})).count("Reminder ") == 3
    assert await host.call("reminders_cancel", {"id": 2}) == "Cancelled."
    now[0] = datetime(2026, 10, 12, 10, 0)
    sched.tick(now[0])
    assert spoken == ["Reminder: stretch. This was due earlier.", "Reminder: call the dentist."]


async def test_a_time_that_has_passed_today_means_tomorrow_and_junk_is_refused(tmp_path):
    host, sched, _, now = build(tmp_path)
    now[0] = datetime(2026, 10, 12, 18, 0)
    assert "tomorrow at 07:30" in await host.call("reminders_set", {"text": "bus", "at": "07:30"})
    for bad in ({"text": "x"}, {"text": "x", "at": "banana"}, {"text": "x", "in_minutes": -5},
                {"text": "x", "at": "2024-01-01T10:00"}, {"text": "x", "at": "2031-01-01T10:00"}, {"at": "10:00"}):
        assert (await host.call("reminders_set", bad)).startswith("Error"), bad


async def test_snooze(tmp_path):
    host, sched, spoken, now = build(tmp_path)
    await host.call("reminders_set", {"text": "tea", "in_minutes": 5})
    out = await host.call("reminders_snooze", {"id": 1, "minutes": 15})
    assert "today at 09:15" in out
