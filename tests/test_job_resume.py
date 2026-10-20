"""Checkpoints and resuming an interrupted job (W1-06)."""
import asyncio

from minty_core.commands import CommandRouter
from minty_core.jobs import JobRunner, interrupted_notice, make_job_store

ITEMS = ["a", "b", "c", "d", "e"]


def runner(tmp_path, **kw):
    return JobRunner(make_job_store(tmp_path, "sqlite"), **kw)


def make_work(done_log, cut_after=None):
    async def work(ctx):
        finished = list(ctx.resumed_from.get("finished", []))
        for item in ITEMS:
            if item in finished:
                continue
            if cut_after is not None and len(finished) >= cut_after:
                raise asyncio.CancelledError  # the program stops here
            done_log.append(item)
            finished.append(item)
            ctx.checkpoint({"finished": finished})
        return "all " + ",".join(finished)
    return work


async def cut_off(tmp_path, cut_after=2, **kw):
    r = runner(tmp_path)
    log = []
    j = r.submit("research", "do the five things", make_work(log, cut_after), announce=False, resumable=True,
                 payload={"things": ITEMS})
    await r.wait(j.id)
    return j, log


async def test_a_checkpoint_survives_the_restart_and_the_job_is_resumable(tmp_path):
    j, log = await cut_off(tmp_path)
    assert log == ["a", "b"] and j.status == "interrupted"
    after = runner(tmp_path)
    after.register_resumer("research", lambda job: make_work([]))
    again = after.get(j.id)
    assert again.status == "interrupted" and again.checkpoint == {"finished": ["a", "b"]} and again.resumable
    assert [x.id for x in after.resumable()] == [j.id]
    assert "resume" in interrupted_notice([again], {again.id}).lower()
    assert "resume" not in interrupted_notice([again]).lower()


async def test_resuming_continues_from_the_checkpoint_not_from_zero(tmp_path):
    j, _ = await cut_off(tmp_path)
    after = runner(tmp_path)
    log = []
    after.register_resumer("research", lambda job: make_work(log))
    resumed = after.resume(j.id)
    assert resumed is not None and resumed.id == j.id
    await after.wait(j.id)
    assert log == ["c", "d", "e"]  # only what was left
    assert after.get(j.id).status == "done" and after.get(j.id).result == "all a,b,c,d,e"
    assert after.get(j.id).resumed == 1
    assert after.resume(j.id) is None  # finished: nothing to resume


async def test_without_a_resumer_or_the_flag_a_job_is_only_reported(tmp_path):
    r = runner(tmp_path)

    async def work(ctx):
        raise asyncio.CancelledError

    flagged = r.submit("research", "x", work, announce=False, resumable=True)
    plain = r.submit("research", "y", work, announce=False)
    await r.wait(flagged.id)
    await r.wait(plain.id)
    after = runner(tmp_path)
    assert after.resumable() == []  # no resumer registered for the kind yet
    after.register_resumer("research", lambda job: make_work([]))
    assert [j.id for j in after.resumable()] == [flagged.id]  # the plain one was never flagged
    assert after.resume(plain.id) is None


async def test_resume_can_be_switched_off(tmp_path):
    j, _ = await cut_off(tmp_path)
    after = runner(tmp_path, resume=False)
    after.register_resumer("research", lambda job: make_work([]))
    assert after.resumable() == [] and after.resume(j.id) is None


async def test_by_voice(tmp_path):
    j, _ = await cut_off(tmp_path)
    after = runner(tmp_path)
    log = []
    after.register_resumer("research", lambda job: make_work(log))
    commands = CommandRouter(agent=None, jobs=after)
    assert f"{j.id}" in commands.handle("what can you resume") and "do the five things" in commands.handle(
        "what can you resume")
    reply = commands.handle("resume it")
    assert reply.startswith("Resuming") and after.get(j.id).status in ("queued", "running", "done")
    await after.wait(j.id)
    assert log == ["c", "d", "e"]
    assert commands.handle("resume it") == "There's nothing to resume."


async def test_a_number_is_needed_when_several_wait(tmp_path):
    r = runner(tmp_path)
    first = r.submit("research", "first", make_work([], 1), announce=False, resumable=True)
    second = r.submit("research", "second", make_work([], 1), announce=False, resumable=True)
    await r.wait(first.id)
    await r.wait(second.id)
    after = runner(tmp_path)
    after.register_resumer("research", lambda job: make_work([]))
    commands = CommandRouter(agent=None, jobs=after)
    assert "which" in commands.handle("resume it").lower()
    assert commands.handle(f"resume job {second.id}").startswith("Resuming")
