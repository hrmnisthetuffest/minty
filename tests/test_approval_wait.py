"""A background job waits for the user's answer to a question the safety gate already asked, for as long as a background job should."""
import asyncio

from minty_core.approvals import ApprovalStore
from minty_core.jobs import JobOutcome, JobRunner, make_job_store


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


async def test_an_adopted_question_is_given_hours_not_minutes_and_the_answer_reaches_the_job(tmp_path):
    clock = Clock()
    approvals = ApprovalStore(tmp_path / "a.sqlite", clock=clock)
    runner = JobRunner(make_job_store(tmp_path, "sqlite"), approvals=approvals)
    seen = {}

    async def work(ctx):
        seen["answer"] = await ctx.wait_on_open_approval("Post this reel?", poll_s=0.01, ttl_s=6 * 3600)
        return "ok"

    record = approvals.request("studio_send", {"id": 1}, "Post this reel?", 3)  # what the gate does: five minutes
    assert record.expires - clock() == 300
    job = runner.submit("mission", "daily reel", work, announce=False)
    await asyncio.sleep(0.1)
    assert approvals.get(record.id).expires - clock() > 5 * 3600 and approvals.get(record.id).job_id == job.id
    approvals.decide(record.id, True, via="test")
    await runner.wait(job.id)
    assert seen["answer"] is True


async def test_an_unanswered_question_expires_and_the_job_can_tell_that_from_a_no(tmp_path):
    clock = Clock()
    approvals = ApprovalStore(tmp_path / "a.sqlite", clock=clock)
    runner = JobRunner(make_job_store(tmp_path, "sqlite"), approvals=approvals)
    seen = {}

    async def work(ctx):
        seen["answer"] = await ctx.wait_on_open_approval("Post this reel?", poll_s=0.01, ttl_s=3600)
        seen["how"] = ctx.last_approval
        return "ok"

    approvals.request("studio_send", {"id": 1}, "Post this reel?", 3)
    job = runner.submit("mission", "daily reel", work, announce=False)
    await asyncio.sleep(0.1)
    clock.t += 7200
    await runner.wait(job.id)
    assert seen == {"answer": False, "how": "expired"}

    approvals2 = ApprovalStore(tmp_path / "b.sqlite", clock=clock)
    runner2 = JobRunner(make_job_store(tmp_path / "x", "sqlite"), approvals=approvals2)
    r2 = approvals2.request("studio_send", {"id": 2}, "Post that?", 3)

    async def work2(ctx):
        seen["answer2"] = await ctx.wait_on_open_approval("Post that?", poll_s=0.01)
        seen["how2"] = ctx.last_approval
        return "ok"

    job2 = runner2.submit("mission", "x", work2, announce=False)
    await asyncio.sleep(0.1)
    approvals2.decide(r2.id, False, via="test")
    await runner2.wait(job2.id)
    assert seen["answer2"] is False and seen["how2"] == "denied"
