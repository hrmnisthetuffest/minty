"""A job's time budget counts the time it is working, not the time it waits for the user to answer a question.

Mission 14 (move every promotion in a Gmail account to trash) sat for its whole 30 minutes on one question nobody was there to answer, and was
reported as "took too long, so I stopped it"."""
import asyncio
import time

from minty_core.agentloop import Budget
from minty_core.approvals import ApprovalStore
from minty_core.jobs import JobRunner, make_job_store


def setup(tmp_path):
    approvals = ApprovalStore(tmp_path / "a.sqlite")
    return approvals, JobRunner(make_job_store(tmp_path, "sqlite"), approvals=approvals)


async def until(predicate, seconds=3.0):
    end = time.monotonic() + seconds
    while not predicate() and time.monotonic() < end:
        await asyncio.sleep(0.01)
    assert predicate()


async def test_waiting_for_your_answer_does_not_use_up_the_budget(tmp_path):
    approvals, runner = setup(tmp_path)

    async def work(ctx):
        ok = await ctx.ask_approval("mission_step", {"p": 1}, "Trash 40 emails?", poll_s=0.01)
        await asyncio.sleep(0.15)  # the real work, well inside the budget
        return "done" if ok else "declined"

    job = runner.submit("mission", "tidy the inbox", work, budget_s=0.5, announce=False)
    await until(lambda: job.status == "waiting_approval")
    await asyncio.sleep(0.8)  # nobody answers for longer than the whole budget
    assert job.status == "waiting_approval"  # still waiting, not stopped
    approvals.decide(job.approval_id, True, via="test")
    await runner.wait(job.id)
    assert job.status == "done" and job.result == "done"


async def test_a_question_the_gate_already_asked_does_not_use_it_up_either(tmp_path):
    approvals, runner = setup(tmp_path)

    async def work(ctx):
        ok = await ctx.wait_on_open_approval("Trash 40 emails?", poll_s=0.01, ttl_s=3600)
        await asyncio.sleep(0.1)
        return "done" if ok else "declined"

    approvals.request("mcp_call", {"n": 1}, "Trash 40 emails?", 3)
    job = runner.submit("mission", "tidy the inbox", work, budget_s=0.4, announce=False)
    await until(lambda: job.status == "waiting_approval")
    await asyncio.sleep(0.6)
    approvals.decide(job.approval_id, True, via="test")
    await runner.wait(job.id)
    assert job.status == "done"


async def test_work_that_really_runs_too_long_is_still_stopped_after_a_wait(tmp_path):
    approvals, runner = setup(tmp_path)

    async def work(ctx):
        await ctx.ask_approval("mission_step", {"p": 2}, "Go on?", poll_s=0.01)
        await asyncio.sleep(5)
        return "never"

    job = runner.submit("mission", "endless", work, budget_s=0.3, announce=False)
    await until(lambda: job.status == "waiting_approval")
    await asyncio.sleep(0.5)
    approvals.decide(job.approval_id, True, via="test")
    await runner.wait(job.id)
    assert job.status == "timeout" and "Stopped after" in job.result


def test_the_mission_loops_own_clock_leaves_out_the_waiting_too():
    spent = Budget(seconds=0.05)
    time.sleep(0.1)
    assert spent.exhausted() == "time"
    waited = Budget(seconds=0.05, waited=lambda: 10.0)
    time.sleep(0.1)
    assert waited.exhausted() == ""


def test_missions_get_two_hours_of_work_by_default_not_half_an_hour():
    from minty_core.plugins.builtin.missions import plugin as missions

    assert missions.DEFAULT_RUNTIME_S == 7200.0
