import asyncio
import time

from minty_core import loopwatch


async def test_a_blocked_loop_is_traced_and_a_free_one_is_not():
    events = []

    class T:
        def event(self, kind, **data):
            events.append((kind, data))

    task = asyncio.create_task(loopwatch.watch(T(), tick=0.02))
    await asyncio.sleep(0.15)
    assert events == []
    time.sleep(0.35)  # something blocking on the loop
    await asyncio.sleep(0.1)
    task.cancel()
    assert events and events[0][0] == "loop_stall" and events[0][1]["ms"] >= 250
