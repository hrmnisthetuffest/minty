import asyncio
from datetime import date, datetime, time, timedelta

import pytest

from minty_core.scheduler import Scheduler

MON = date(2026, 10, 12)


def at(hh, mm=0, d=MON):
    return datetime(d.year, d.month, d.day, hh, mm)


def until_half_past_three(now):
    return now.time() < time(15, 30)


class Harness:
    """A scheduler whose gate holds Minty's own items until 15:30 (what the gate is does not matter to the scheduler)."""

    def __init__(self, tmp_path, hold=until_half_past_three):
        self.spoken, self.shown, self.now = [], [], at(8)
        self.sched = Scheduler(tmp_path / "s.db", speak=self.spoken.append, show=self.shown.append, clock=lambda: self.now,
                               gate=lambda item, now: "hold" if hold(now) else "say")

    def tick(self, now):
        self.now = now
        return self.sched.tick(now)


def test_a_reminder_fires_exactly_then_and_only_once(tmp_path):
    h = Harness(tmp_path)
    h.sched.add("call the dentist", at(10, 0))
    assert h.tick(at(9, 59)) == [] and h.spoken == []
    assert [i.text for i in h.tick(at(10, 0))] == ["call the dentist"]
    assert h.spoken == ["Reminder: call the dentist."] and h.shown == ["call the dentist"]
    assert h.tick(at(10, 1)) == [] and h.sched.waiting() == []


def test_mintys_own_nudges_are_held_silently_then_one_digest(tmp_path):
    h = Harness(tmp_path)
    h.sched.add("Maths exercise 4 is due tomorrow", at(9, 0), user_set=False, kind="nudge", key="hw:1")
    h.sched.add("New notice: sports day on Friday", at(11, 0), user_set=False, kind="nudge", key="n:1")
    h.tick(at(9, 5))
    h.tick(at(11, 5))
    assert h.spoken == []  # nothing spoken while the gate holds
    assert h.shown == ["Maths exercise 4 is due tomorrow", "New notice: sports day on Friday"]  # shown quietly
    assert [i.status for i in h.sched.waiting()] == ["held", "held"]
    h.tick(at(15, 29))
    assert h.spoken == []
    h.tick(at(15, 30))
    assert h.spoken == ["Earlier: Maths exercise 4 is due tomorrow; New notice: sports day on Friday."]
    assert h.sched.waiting() == []


def test_the_user_reminder_still_speaks_while_nudges_wait(tmp_path):
    h = Harness(tmp_path)
    h.sched.add("homework nudge", at(9), user_set=False, kind="nudge")
    h.sched.add("take medicine", at(12), user_set=True)
    h.tick(at(9, 1))
    h.tick(at(12, 0))
    assert h.spoken == ["Reminder: take medicine."]


def test_nothing_is_held_when_the_gate_lets_it_through(tmp_path):
    h = Harness(tmp_path, hold=lambda n: False)
    h.sched.add("nudge", at(10), user_set=False, kind="nudge")
    h.tick(at(10, 1))
    assert h.spoken == ["nudge"]


def test_a_missed_reminder_fires_once_late_when_the_computer_comes_back(tmp_path):
    h = Harness(tmp_path)
    item = h.sched.add("pay the bill", at(8, 0))
    out = h.tick(at(18, 0))
    assert h.spoken == ["Reminder: pay the bill. This was due earlier."] and out[0].id == item.id
    assert h.sched.get(item.id).late and h.tick(at(18, 1)) == []


def test_the_same_key_is_never_queued_twice_but_can_be_again_after_it_fired(tmp_path):
    h = Harness(tmp_path)
    assert h.sched.add("n", at(9), user_set=False, key="hw:1") is not None
    assert h.sched.add("n again", at(9), user_set=False, key="hw:1") is None
    h.tick(at(16))
    assert h.sched.add("n later", at(17), user_set=False, key="hw:1") is not None


def test_cancel_snooze_and_validation(tmp_path):
    h = Harness(tmp_path)
    a = h.sched.add("one", at(10))
    b = h.sched.add("two", at(10, 5))
    assert h.sched.cancel(a.id) and not h.sched.cancel(a.id)
    h.tick(at(10, 1))
    assert h.spoken == []
    h.now = at(10, 4)
    assert h.sched.snooze(b.id, 20).due == at(10, 24)
    h.tick(at(10, 10))
    assert h.spoken == [] and h.tick(at(10, 25)) and h.spoken == ["Reminder: two."]
    with pytest.raises(ValueError):
        h.sched.add("   ", at(10))


def test_long_digests_are_cut_with_a_count(tmp_path):
    h = Harness(tmp_path)
    for i in range(40):
        h.sched.add(f"Notice number {i} about the annual sports day arrangements", at(9), user_set=False,
                    kind="nudge", key=f"k{i}")
    h.tick(at(9, 1))
    h.tick(at(15, 31))
    assert len(h.spoken) == 1 and len(h.spoken[0]) < 500 and "more." in h.spoken[0]


async def test_the_background_task_wakes_for_a_new_item(tmp_path):
    spoken = []
    now = [datetime(2026, 10, 12, 20, 0, 0)]
    sched = Scheduler(tmp_path / "s.db", speak=spoken.append, show=lambda t: None, clock=lambda: now[0])
    task = asyncio.create_task(sched.run())
    await asyncio.sleep(0.05)
    sched.add("now please", now[0] - timedelta(seconds=1))
    await asyncio.sleep(0.4)
    task.cancel()
    assert spoken == ["Reminder: now please."]
