"""A job's work can end in a status of its own (a mission that is blocked on a question, or failed with a reason)."""
import asyncio

from minty_core.jobs import FINAL, JobOutcome, JobRunner, make_job_store


def runner(tmp_path, **kw):
    said = []
    return JobRunner(make_job_store(tmp_path, "sqlite"), notify=said.append, **kw), said


def ends(status, text):
    async def work(ctx):
        return JobOutcome(status, text)
    return work


async def test_a_work_function_can_end_the_job_as_failed_with_its_own_words(tmp_path):
    r, said = runner(tmp_path)
    j = r.submit("mission", "tidy the notes", ends("failed", "I got stuck on step 2: nothing changed."))
    await r.wait(j.id)
    assert j.status == "failed" and j.result == "I got stuck on step 2: nothing changed."
    assert "failed" in said[0] and "stuck on step 2" in said[0]


async def test_a_blocked_job_is_finished_but_holds_the_question_and_tells_the_user(tmp_path):
    r, said = runner(tmp_path)
    j = r.submit("mission", "book the thing", ends("blocked", "Which date do you want?"))
    await r.wait(j.id)
    assert j.status == "blocked" and "blocked" in FINAL and not j.active
    assert j.question == "Which date do you want?"
    assert "needs you" in said[0] and "Which date" in said[0]


async def test_an_unknown_outcome_status_is_a_failure_not_a_silent_success(tmp_path):
    r, _ = runner(tmp_path)
    j = r.submit("mission", "x", ends("great-success", "yay"))
    await r.wait(j.id)
    assert j.status == "failed" and "great-success" in j.result


def make_work(log):
    async def work(ctx):
        log.append(ctx.resumed_from.get("note", "fresh"))
        return "ok"
    return work


async def test_a_blocked_or_failed_job_can_be_resumed_but_a_finished_one_cannot(tmp_path):
    r, _ = runner(tmp_path)
    log = []
    r.register_resumer("mission", lambda job: make_work(log))
    blocked = r.submit("mission", "a", ends("blocked", "q?"), announce=False, resumable=True)
    failed = r.submit("mission", "b", ends("failed", "no"), announce=False, resumable=True)
    done = r.submit("mission", "c", ends("done", "yes"), announce=False, resumable=True)
    plain = r.submit("mission", "d", ends("failed", "no"), announce=False)  # never flagged resumable
    for j in (blocked, failed, done, plain):
        await r.wait(j.id)
    assert [j.id for j in r.resumable()] == [blocked.id]  # only waiting-for-you jobs are offered by themselves
    assert r.resume(done.id) is None and r.resume(plain.id) is None
    assert r.resume(failed.id) is not None and r.resume(blocked.id) is not None
    await r.wait(failed.id)
    await r.wait(blocked.id)
    assert failed.status == "done" and blocked.status == "done" and failed.resumed == 1 and len(log) == 2
