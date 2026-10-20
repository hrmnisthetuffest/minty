"""The voice loop restarts on its own, bounded, without touching the core (W1-26)."""
import asyncio
import time

import pytest

from tests.test_pipeline import build, finish, wait_until
from minty_core.supervisor import VoiceSupervisor


async def test_a_failing_loop_is_restarted_and_the_user_is_told_once():
    runs, said = [], []

    async def loop():
        runs.append(1)
        if len(runs) < 3:
            raise RuntimeError("PortAudio error")
        await asyncio.sleep(60)

    sup = VoiceSupervisor(loop, notify=said.append, delay_s=0.0)
    task = asyncio.create_task(sup.run())
    await wait_until(lambda: len(runs) == 3)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert sup.restarts == 2 and not sup.gave_up
    assert said == ["My voice stopped for a moment; I'm bringing it back."]


async def test_it_gives_up_after_too_many_failures_in_the_window_and_says_so():
    said = []

    async def always():
        raise RuntimeError("no microphone")

    sup = VoiceSupervisor(always, notify=said.append, delay_s=0.0, max_restarts=3, window_s=60)
    await asyncio.wait_for(sup.run(), 2)
    assert sup.gave_up and sup.restarts == 3
    assert said[-1].startswith("My voice keeps failing")


async def test_old_failures_age_out_of_the_window():
    now = {"t": 0.0}
    runs = []

    async def sleeper(_s):
        now["t"] += 100  # each restart happens 100 s after the last

    async def flaky():
        runs.append(1)
        if len(runs) < 8:
            raise RuntimeError("x")
        await asyncio.sleep(60)

    sup = VoiceSupervisor(flaky, delay_s=0.0, max_restarts=2, window_s=150, sleep=sleeper, clock=lambda: now["t"])
    task = asyncio.create_task(sup.run())
    await wait_until(lambda: len(runs) == 8)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert not sup.gave_up and sup.restarts == 7


async def test_cancelling_stops_it_without_a_restart():
    async def loop():
        await asyncio.sleep(60)

    sup = VoiceSupervisor(loop, delay_s=0.0)
    task = asyncio.create_task(sup.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sup.restarts == 0


async def test_the_real_pipeline_comes_back_within_five_seconds_and_still_answers(tmp_path):
    p, f = build(tmp_path, stt_texts=["what time is it"], replies=[["It is noon."]])
    original = p._read
    state = {"broken": 0}

    async def read():
        if state["broken"] == 0:
            state["broken"] = 1
            raise RuntimeError("the audio device went away")
        return await original()

    p._read = read
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    started = time.monotonic()
    sup = VoiceSupervisor(p.run, delay_s=0.2)
    task = asyncio.create_task(sup.run())
    await wait_until(lambda: b"It is noon." in f.player.played)
    assert time.monotonic() - started < 5.0 and sup.restarts == 1
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await finish(p, f, asyncio.create_task(asyncio.sleep(0)))


async def test_restart_on_request_starts_the_loop_again_without_counting_a_failure():
    runs = []

    async def loop():
        runs.append(1)
        await asyncio.sleep(60)

    sup = VoiceSupervisor(loop, delay_s=0.0)
    task = asyncio.create_task(sup.run())
    await wait_until(lambda: len(runs) == 1)
    assert sup.restart() is True
    await wait_until(lambda: len(runs) == 2)
    assert sup.restarts == 0 and not sup.gave_up
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert sup.restart() is False
