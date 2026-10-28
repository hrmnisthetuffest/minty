import asyncio
import threading
from types import SimpleNamespace

from minty_core.app import _attention_loop


def ctx(enabled=True, attention=None):
    cfg = SimpleNamespace(proactive=SimpleNamespace(attention=enabled))
    return SimpleNamespace(store=SimpleNamespace(config=cfg), services={"attention": attention} if attention else {})


class Counting:
    def __init__(self, fail=False):
        self.ticks, self.fail, self.threads = 0, fail, set()

    def tick(self):
        self.threads.add(threading.get_ident())
        self.ticks += 1
        if self.fail:
            raise RuntimeError("boom")


async def run_for(c, seconds=0.2, **kw):
    task = asyncio.create_task(_attention_loop(c, first_s=0.0, every_s=0.02, **kw))
    await asyncio.sleep(seconds)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_it_looks_again_and_again():
    att = Counting()
    await run_for(ctx(attention=att))
    assert att.ticks >= 3


async def test_the_switch_turns_it_off():
    att = Counting()
    await run_for(ctx(enabled=False, attention=att))
    assert att.ticks == 0


async def test_a_failing_look_does_not_end_the_loop():
    att = Counting(fail=True)
    await run_for(ctx(attention=att))
    assert att.ticks >= 3


async def test_without_the_plugin_it_just_waits():
    await run_for(ctx(), seconds=0.1)


async def test_it_looks_on_the_event_loop_because_the_things_it_reads_are_changed_there():
    att = Counting()
    await run_for(ctx(attention=att))
    assert att.threads == {threading.get_ident()}


async def test_the_recipe_clock_starts_what_is_due_every_minute_and_survives_a_failure():
    from minty_core.app import _recipes_loop

    class Clock:
        def __init__(self, fail=False):
            self.ticks, self.fail, self.threads = 0, fail, set()

        def tick(self, now=None, halted=False):
            self.threads.add(threading.get_ident())
            self.ticks += 1
            if self.fail:
                raise RuntimeError("boom")

    for fail in (False, True):
        rec = Clock(fail)
        c = SimpleNamespace(services={"recipes": rec})
        task = asyncio.create_task(_recipes_loop(c, every_s=0.02))
        await asyncio.sleep(0.2)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert rec.ticks >= 3 and rec.threads == {threading.get_ident()}
    await asyncio.wait_for(_recipes_loop(SimpleNamespace(services={}), every_s=0.01, rounds=2), 1)  # no recipes plugin: waits, ends


async def test_the_recipe_clock_does_not_start_anything_during_a_lockdown():
    from minty_core.app import _recipes_loop

    class Clock:
        def __init__(self):
            self.halted_seen = []

        def tick(self, now=None, halted=False):
            self.halted_seen.append(halted)

    rec, lock = Clock(), SimpleNamespace(active=True)
    c = SimpleNamespace(services={"recipes": rec, "lockdown": lock})
    task = asyncio.create_task(_recipes_loop(c, every_s=0.02))
    await asyncio.sleep(0.1)
    lock.active = False
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert True in rec.halted_seen and False in rec.halted_seen
