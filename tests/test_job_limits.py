"""Concurrency and exclusive resources hold under load (W1-07)."""
import asyncio

from minty_core.jobs import JobRunner, make_job_store


class Meter:
    def __init__(self):
        self.now = self.peak = 0
        self.by_key: dict[str, int] = {}
        self.peak_key: dict[str, int] = {}

    def enter(self, key):
        self.now += 1
        self.peak = max(self.peak, self.now)
        if key:
            self.by_key[key] = self.by_key.get(key, 0) + 1
            self.peak_key[key] = max(self.peak_key.get(key, 0), self.by_key[key])

    def leave(self, key):
        self.now -= 1
        if key:
            self.by_key[key] -= 1


async def test_twenty_jobs_never_exceed_the_slots_or_share_a_resource(tmp_path):
    runner = JobRunner(make_job_store(tmp_path, "sqlite"), max_concurrent=3)
    meter = Meter()

    def work_for(key):
        async def work(ctx):
            meter.enter(key)
            try:
                await asyncio.sleep(0.01)
            finally:
                meter.leave(key)
            return "ok"
        return work

    keys = ["browser", "audio", None, "browser", None]
    jobs = [runner.submit("research", f"job {i}", work_for(keys[i % 5]), exclusive=keys[i % 5], announce=False)
            for i in range(20)]
    for j in jobs:
        await runner.wait(j.id)
    assert all(j.status == "done" for j in jobs)
    assert meter.peak <= 3
    assert meter.peak_key == {"browser": 1, "audio": 1}


async def test_a_waiting_exclusive_job_does_not_hold_a_slot_that_others_could_use(tmp_path):
    runner = JobRunner(make_job_store(tmp_path, "sqlite"), max_concurrent=2)
    release = asyncio.Event()
    started = []

    async def browser(ctx):
        started.append(ctx.job.goal)
        await release.wait()
        return "ok"

    async def other(ctx):
        started.append(ctx.job.goal)
        return "ok"

    first = runner.submit("browser", "browser 1", browser, exclusive="browser", announce=False)
    second = runner.submit("browser", "browser 2", browser, exclusive="browser", announce=False)
    third = runner.submit("research", "other", other, announce=False)
    await asyncio.wait_for(runner.wait(third.id), 2)  # must finish while both browser jobs are still unreleased
    assert "other" in started and started == ["browser 1", "other"]
    release.set()
    await runner.wait(first.id)
    await runner.wait(second.id)
    assert (first.status, second.status, third.status) == ("done", "done", "done")
