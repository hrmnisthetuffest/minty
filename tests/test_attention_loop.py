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
