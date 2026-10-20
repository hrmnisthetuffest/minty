"""Bounded retries and jobs that wait for an approval (W1-05)."""
import asyncio

from minty_core.approvals import ApprovalStore
from minty_core.jobs import JobRunner, make_job_store


def runner(tmp_path, **kw):
    return JobRunner(make_job_store(tmp_path, "sqlite"), **kw)


class Flaky:
    def __init__(self, fail_times):
        self.fail_times, self.calls = fail_times, 0

    async def __call__(self, ctx):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(f"network down ({self.calls})")
        return "Finished."


async def test_a_job_that_fails_twice_succeeds_on_the_third_attempt(tmp_path):
    r = runner(tmp_path, max_attempts=3, retry_delay_s=0)
    work = Flaky(2)
    j = r.submit("research", "look it up", work, announce=False)
    await r.wait(j.id)
    assert (j.status, j.result, j.attempts, work.calls) == ("done", "Finished.", 3, 3)
    assert any("attempt 2 of 3" in p for p in j.progress)


async def test_retries_stop_at_the_bound(tmp_path):
    r = runner(tmp_path, max_attempts=3, retry_delay_s=0)
    work = Flaky(99)
    j = r.submit("research", "look it up", work, announce=False)
    await r.wait(j.id)
    assert j.status == "failed" and work.calls == 3 and j.attempts == 3
    assert "3 attempts" in j.result and "network down (3)" in j.result


async def test_by_default_nothing_is_retried(tmp_path):
    r = runner(tmp_path)
    work = Flaky(99)
    j = r.submit("research", "look it up", work, announce=False)
    await r.wait(j.id)
    assert j.status == "failed" and work.calls == 1 and j.attempts == 1


async def test_a_timeout_is_not_retried(tmp_path):
    r = runner(tmp_path, max_attempts=3, retry_delay_s=0)
    calls = []

    async def slow(ctx):
        calls.append(1)
        await asyncio.sleep(5)

    j = r.submit("research", "slow", slow, budget_s=0.05, announce=False)
    await r.wait(j.id)
    assert j.status == "timeout" and len(calls) == 1


async def test_cancelling_during_the_back_off_stops_the_job(tmp_path):
    r = runner(tmp_path, max_attempts=3, retry_delay_s=30)
    work = Flaky(99)
    j = r.submit("research", "look it up", work, announce=False)
    for _ in range(100):
        await asyncio.sleep(0.01)
        if work.calls == 1 and j.progress:
            break
    assert await r.cancel(j.id) is True
    await r.wait(j.id)
    assert j.status == "cancelled" and work.calls == 1


async def test_a_job_waits_for_an_approval_and_goes_on_when_it_is_given(tmp_path):
    approvals = ApprovalStore(tmp_path / "approvals.sqlite")
    said = []
    r = runner(tmp_path, approvals=approvals, notify=said.append)
    seen = {}

    async def work(ctx):
        seen["answer"] = await ctx.ask_approval("mail_send", {"to": "teacher"}, "Send the mail to your teacher?",
                                                poll_s=0.01)
        return "Sent." if seen["answer"] else "Not sent."

    j = r.submit("research", "mail the teacher", work, announce=False)
    for _ in range(100):
        await asyncio.sleep(0.01)
        if j.status == "waiting_approval":
            break
    assert j.status == "waiting_approval" and j.approval_id is not None and j.active
    assert any("needs your approval" in s for s in said)
    assert make_job_store(tmp_path, "sqlite")._read()[j.id].status == "waiting_approval"  # on disk, not just in memory
    approvals.decide(j.approval_id, True, via="voice")
    await r.wait(j.id)
    assert j.status == "done" and seen["answer"] is True and j.result == "Sent."
    assert approvals.get(j.approval_id).status == "used"


async def test_a_denied_or_expired_approval_is_a_no(tmp_path):
    clock = {"now": 1000.0}
    approvals = ApprovalStore(tmp_path / "approvals.sqlite", clock=lambda: clock["now"], ttl_s=30)
    r = runner(tmp_path, approvals=approvals)
    answers = []

    async def work(ctx):
        answers.append(await ctx.ask_approval("files_delete", {"p": "x"}, "Delete x?", poll_s=0.01))
        return "ok"

    denied = r.submit("research", "d", work, announce=False)
    for _ in range(100):
        await asyncio.sleep(0.01)
        if denied.status == "waiting_approval":
            break
    approvals.decide(denied.approval_id, False)
    await r.wait(denied.id)
    expired = r.submit("research", "e", work, announce=False)
    for _ in range(100):
        await asyncio.sleep(0.01)
        if expired.status == "waiting_approval":
            break
    clock["now"] += 31
    await r.wait(expired.id)
    assert answers == [False, False]
