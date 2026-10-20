"""The point of the job runner: the voice keeps answering while long work runs in the background."""
import asyncio
import time

from tests.test_pipeline import build, finish, wait_until
from minty_core.jobs import JobRunner


async def test_voice_answers_while_a_long_job_runs_and_the_result_comes_after(tmp_path):
    p, f = build(tmp_path, stt_texts=["what time is it"], replies=[["It is noon."]])
    runner = JobRunner(None, bus=f.bus, notify=lambda text: f.bus.publish("notify", text=text))

    async def long_browser_work(ctx):
        ctx.progress("opening the shop")
        await asyncio.sleep(1.5)
        return "The cheapest lamp is the Reading Lamp."

    task = asyncio.create_task(p.run())
    runner.submit("browser", "find the cheapest lamp", long_browser_work, exclusive="browser")
    await asyncio.sleep(0.05)
    started = time.monotonic()
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: len(f.player.played) >= 1, timeout=2.0)
    answered_in = time.monotonic() - started
    assert answered_in < 1.0, answered_in  # not stuck behind the 1.5 s job
    assert runner.running() and runner.running()[0].status == "running"
    await wait_until(lambda: len(f.player.played) >= 2, timeout=4.0)
    assert f.player.played[0] == b"It is noon." and f.player.played[1] == b"The cheapest lamp is the Reading Lamp."
    await finish(p, f, task)
