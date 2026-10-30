"""The watchdog watches work from outside it and can stop it: stalled or looping jobs, state that does not match, a workflow that keeps failing (mind/watchdog.py)."""
import asyncio
from types import SimpleNamespace as NS

import pytest

from minty_core.approvals import ApprovalStore
from minty_core.flows.store import FlowStore
from minty_core.jobs import Job, JobRunner, make_job_store
from minty_core.mind.watchdog import Watchdog


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


class Told:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)

    def titles(self):
        return [(e.severity, e.title) for e in self.events]


@pytest.fixture
def world(tmp_path):
    clock, told = Clock(), Told()
    approvals = ApprovalStore(tmp_path / "a.sqlite", clock=clock)
    jobs = JobRunner(make_job_store(tmp_path, "sqlite"), clock=clock, approvals=approvals)
    return NS(clock=clock, told=told, jobs=jobs, approvals=approvals, dog=Watchdog(jobs, None, lambda: told, clock), tmp=tmp_path)


async def hang(ctx):
    await asyncio.sleep(1000)


async def start(w, work=hang, kind="mission"):
    job = w.jobs.submit(kind, "tidy the files", work, budget_s=100000, announce=False)
    await asyncio.sleep(0.05)
    return job


# ---- stalled and looping work ------------------------------------------------------------------------------------------
async def test_work_with_no_sign_of_life_is_flagged_once_and_then_stopped_with_the_reason_on_the_job(world):
    job = await start(world)
    world.clock.t += 1000  # 16 minutes of silence: over the 15 a mission is given
    assert [d["did"] for d in await world.dog.tick()] == ["stalled"] and job.health == "stalled" and job.status == "running"
    assert await world.dog.tick() == []  # said once, not every half minute
    assert world.told.titles() == [("notable", "Mission 1 has stopped moving")]
    world.clock.t += 2000
    assert [d["did"] for d in await world.dog.tick()] == ["cancelled"]
    await asyncio.sleep(0.05)
    assert job.status == "cancelled" and job.result.startswith("Stopped by the watchdog: no progress for") and world.told.titles()[-1] == ("important", "I stopped mission 1")


async def test_long_work_that_keeps_moving_is_left_alone_however_long_it_takes(world):
    async def busy(ctx):
        for i in range(500):
            ctx.progress(f"found {i} items")
            await asyncio.sleep(0.01)
    job = await start(world, busy)
    for _ in range(10):
        world.clock.t += 600  # ten minutes at a time, hours in all: but it keeps reporting
        job.touched = world.clock.t
        assert await world.dog.tick() == []
    assert job.health == "ok" and job.status == "running"


async def test_time_spent_waiting_for_your_answer_is_never_a_stall(world):
    async def ask(ctx):
        return await ctx.ask_approval("mission_step", {"n": 1}, "Trash 40 emails?", poll_s=0.01)
    job = await start(world, ask)
    assert job.status == "waiting_approval"
    world.clock.t += 20_000  # you were away for hours
    assert await world.dog.tick() == [] and job.health == "ok" and job.status == "waiting_approval"
    world.approvals.decide(job.approval_id, True, via="test")
    await asyncio.sleep(0.2)  # answered: the job carries on, and the answer counted as a sign of life
    assert await world.dog.tick() == [] and job.health == "ok"


async def test_the_same_step_over_and_over_is_a_loop_and_far_too_much_of_it_is_stopped(world):
    async def spin(ctx):
        for _ in range(5):
            ctx.progress("using mcp call")
        await asyncio.sleep(1000)
    job = await start(world, spin)
    assert [d["did"] for d in await world.dog.tick()] == ["looping"] and job.health == "looping" and job.status == "running"
    for _ in range(8):
        job.progress.append("using mcp call")
    assert [d["did"] for d in await world.dog.tick()] == ["cancelled"]
    await asyncio.sleep(0.05)
    assert job.status == "cancelled" and "repeated" in job.result


async def test_work_much_slower_than_its_kind_usually_is_marked_slow_but_not_stopped(world):
    for i in range(3):
        world.jobs.jobs[100 + i] = Job(id=100 + i, kind="mission", goal="g", status="done", created=0.0, started=1000.0, finished=1060.0)  # these took a minute
    job = await start(world)
    world.clock.t += 400
    job.touched = world.clock.t  # moving, just long
    assert await world.dog.tick() == [] and job.health == "slow" and job.status == "running"
    assert any(f["kind"] == "slow" for f in world.dog.findings())


async def test_a_job_recorded_as_running_with_nothing_running_it_is_marked_failed_not_left_running_for_ever(world):
    world.jobs.jobs[9] = Job(id=9, kind="mission", goal="ghost work", status="running", created=world.clock.t - 500, started=world.clock.t - 500, touched=world.clock.t - 100)
    assert [d["did"] for d in await world.dog.tick()] == ["marked failed"]
    assert world.jobs.jobs[9].status == "failed" and "nothing was running it" in world.jobs.jobs[9].result


# ---- a workflow that keeps failing ----------------------------------------------------------------------------------------
def flow_world(tmp_path, statuses):
    store = FlowStore(tmp_path / "f.sqlite")
    f = store.create({"name": "digest", "nodes": [{"id": "go", "type": "trigger.manual", "params": {}, "pos": [0, 0]}], "edges": []})
    store.set_enabled(f.id, True)
    for s, mode in statuses:
        rid = store.start_run(f.id, 1, mode, "schedule", {})
        store.finish_run(rid, s, error="smtp refused" if s != "done" else "")
    return store, f


async def test_a_workflow_whose_last_real_runs_all_failed_is_switched_off_once_and_the_owner_is_told(world, tmp_path):
    store, f = flow_world(tmp_path, [("done", "real"), ("failed", "real"), ("blocked", "real"), ("failed", "real")])
    dog = Watchdog(world.jobs, NS(store=store), lambda: world.told, world.clock)
    assert [d["did"] for d in await dog.tick()] == ["switched off"] and store.get(f.id).enabled is False
    assert world.told.titles() == [("important", "I switched off the workflow digest")] and "smtp refused" in world.told.events[0].body
    assert await dog.tick() == []  # it is off now: nothing more to do or say


async def test_a_streak_broken_by_a_success_or_only_failed_tests_do_not_switch_a_workflow_off(world, tmp_path):
    store, f = flow_world(tmp_path, [("failed", "real"), ("failed", "real"), ("done", "real"), ("failed", "real"), ("failed", "test"), ("failed", "test")])
    assert await Watchdog(world.jobs, NS(store=store), lambda: world.told, world.clock).tick() == [] and store.get(f.id).enabled is True


# ---- the watchdog is not the agent's to switch off ----------------------------------------------------------------------------
def test_nothing_the_model_can_call_changes_the_watchdog_or_its_limits(world):
    from minty_core.context import build_context

    ctx = build_context(world.tmp / "app")
    names = [t for p in ctx.services["list_plugins"]() for t in p["tools"]]
    assert not [n for n in names if "watchdog" in n] and "watchdog" in ctx.services  # it runs, and no tool reaches it
