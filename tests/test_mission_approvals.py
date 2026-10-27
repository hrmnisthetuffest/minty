"""A risky mission step through the real safety gate: one question for the user, however it is answered."""
import asyncio

import pytest

from tests.agent_eval.scripted import ScriptedRouter
from minty_core.context import build_context


@pytest.fixture
def ctx(tmp_path):
    c = build_context(tmp_path)
    c.services["router"] = ScriptedRouter([lambda last: ("tool", "files_delete", {"path": "old.txt"}),
                                           lambda last: ("say", "Tried to delete it.")])
    return c


async def start_and_wait_for_the_question(ctx):
    approvals, jobs = ctx.services["approvals"], ctx.services["jobs"]
    await ctx.host.call("missions_start", {"goal": "delete the old notes file from the workspace"})
    job = max(jobs.jobs.values(), key=lambda j: j.id)
    for _ in range(200):
        if approvals.pending():
            break
        await asyncio.sleep(0.02)
    return job, approvals, jobs


async def test_a_risky_step_opens_one_approval_and_the_mission_waits_on_it(ctx):
    job, approvals, jobs = await start_and_wait_for_the_question(ctx)
    pending = approvals.pending()
    assert len(pending) == 1 and "old.txt" in pending[0].question
    assert pending[0].job_id == job.id  # it shows up as the mission's question
    for _ in range(100):
        if job.status == "waiting_approval":
            break
        await asyncio.sleep(0.02)
    assert job.status == "waiting_approval"
    approvals.decide(pending[0].id, True, via="test")
    await jobs.wait(job.id)
    assert job.status in ("done", "failed") and approvals.pending() == []
    step = next(e for e in job.checkpoint["ledger"] if e["tool"] == "files_delete")
    assert "declined" not in step["result"].lower()  # the yes reached the gate and the tool ran


async def test_a_no_stops_the_step_and_is_not_recorded_as_a_success(ctx):
    job, approvals, jobs = await start_and_wait_for_the_question(ctx)
    approvals.decide(approvals.pending()[0].id, False, via="test")
    await jobs.wait(job.id)
    step = next(e for e in job.checkpoint["ledger"] if e["tool"] == "files_delete")
    assert step["result"] == "The user declined." and step["ok"] is False
    assert approvals.pending() == []
