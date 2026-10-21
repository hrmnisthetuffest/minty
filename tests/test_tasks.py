"""spawn(): a background task that nothing else holds is kept alive until it finishes, and its failure is logged instead of lost."""
import asyncio
import gc
import logging

from minty_core.tasks import spawn


async def test_a_spawned_task_runs_to_the_end_even_if_nobody_keeps_it():
    done = []

    async def work():
        await asyncio.sleep(0.05)
        done.append(1)

    spawn(work())
    gc.collect()
    await asyncio.sleep(0.15)
    assert done == [1]


async def test_a_failure_is_logged_and_never_raised(caplog):
    async def boom():
        raise RuntimeError("it broke")

    with caplog.at_level(logging.ERROR):
        spawn(boom(), name="the thing")
        await asyncio.sleep(0.05)
    assert "the thing" in caplog.text and "it broke" in caplog.text


async def test_a_cancelled_task_is_quiet(caplog):
    async def forever():
        await asyncio.sleep(60)

    with caplog.at_level(logging.ERROR):
        task = spawn(forever())
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0.02)
    assert caplog.text == ""


async def test_finished_tasks_are_forgotten():
    from minty_core import tasks

    async def quick():
        return 1

    before = len(tasks._running)
    spawn(quick())
    await asyncio.sleep(0.02)
    assert len(tasks._running) == before
