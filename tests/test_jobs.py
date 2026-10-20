import asyncio
import json

import pytest

from minty_core.events import EventBus
from minty_core.jobs import Job, JobRunner, JobStore, short, two_sentences


def runner(tmp_path=None, **kw):
    said = []
    store = JobStore(tmp_path / "jobs.jsonl") if tmp_path else None
    bus = EventBus()
    r = JobRunner(store, bus=bus, notify=said.append, **kw)
    return r, said, bus


def work(result="All done. Found it. Extra.", delay=0.0, steps=()):
    async def run(ctx):
        for s in steps:
            ctx.progress(s)
        await asyncio.sleep(delay)
        return result
    return run


async def test_job_runs_reports_and_announces(tmp_path):
    r, said, bus = runner(tmp_path)
    q = bus.subscribe({"job"})
    job = r.submit("browser", "find the cheapest lamp", work(steps=["opening shop", "searching"]))
    assert job.id == 1 and job.status == "queued"
    done = await r.wait(job.id)
    assert done.status == "done" and done.result == "All done. Found it. Extra."
    assert done.progress == ["opening shop", "searching"] and done.finished >= done.started
    assert said == ["All done. Found it."]  # two sentences at most
    statuses = []
    while not q.empty():
        statuses.append(q.get_nowait()["status"])
    assert statuses[0] == "queued" and "running" in statuses and statuses[-1] == "done"


async def test_cancel_running_and_queued(tmp_path):
    r, said, _ = runner(tmp_path, max_concurrent=1)
    first = r.submit("subagent", "slow one", work(delay=10))
    second = r.submit("subagent", "waiting one", work(delay=10))
    await asyncio.sleep(0.05)
    assert first.status == "running" and second.status == "queued"
    assert await r.cancel(second.id) and await r.cancel(first.id)
    await asyncio.sleep(0.05)
    assert first.status == second.status == "cancelled"
    assert said == []  # a cancellation the user asked for is not announced
    assert not await r.cancel(first.id)  # second cancel is harmless


async def test_timeout_reports_last_step(tmp_path):
    r, said, _ = runner(tmp_path)
    job = r.submit("browser", "endless", work(delay=5, steps=["clicking Next"]), budget_s=0.1)
    await r.wait(job.id)
    assert job.status == "timeout" and "clicking Next" in job.result
    assert said and "took too long" in said[0]


async def test_failure_is_reported_with_the_reason(tmp_path):
    r, said, _ = runner(tmp_path)

    async def boom(ctx):
        raise RuntimeError("page vanished")

    job = r.submit("browser", "do a thing", boom)
    await r.wait(job.id)
    assert job.status == "failed" and "page vanished" in job.result and "failed" in said[0]


async def test_concurrency_cap_queues_extra_jobs(tmp_path):
    r, _, _ = runner(tmp_path, max_concurrent=2)
    jobs = [r.submit("subagent", f"j{i}", work(delay=0.2)) for i in range(3)]
    await asyncio.sleep(0.05)
    assert [j.status for j in jobs] == ["running", "running", "queued"]
    for j in jobs:
        await r.wait(j.id)
    assert all(j.status == "done" for j in jobs)


async def test_exclusive_resource_serialises(tmp_path):
    r, _, _ = runner(tmp_path)
    order = []

    def tracked(name):
        async def run(ctx):
            order.append(f"start {name}")
            await asyncio.sleep(0.1)
            order.append(f"end {name}")
            return name
        return run

    a = r.submit("browser", "a", tracked("a"), exclusive="browser")
    b = r.submit("browser", "b", tracked("b"), exclusive="browser")
    c = r.submit("think", "c", tracked("c"))
    await asyncio.sleep(0.02)
    assert b.status == "queued" and "start c" in order  # b waits for the window; c is not held up
    for j in (a, b, c):
        await r.wait(j.id)
    assert order.index("end a") < order.index("start b")


async def test_progress_ring_is_bounded(tmp_path):
    r, _, _ = runner(tmp_path)
    job = r.submit("browser", "x", work(steps=[f"step {i}" for i in range(50)]))
    await r.wait(job.id)
    assert len(job.progress) == 20 and job.progress[-1] == "step 49"


async def test_restart_marks_unfinished_jobs_interrupted_once(tmp_path):
    r, said, _ = runner(tmp_path)
    job = r.submit("browser", "long job", work(delay=10))
    await asyncio.sleep(0.05)
    await r.shutdown()  # the program stopped mid-job
    assert job.status == "interrupted" and said == []
    again = JobRunner(JobStore(tmp_path / "jobs.jsonl"))
    assert [j.goal for j in again.interrupted] == ["long job"] and again.get(job.id).status == "interrupted"
    assert JobRunner(JobStore(tmp_path / "jobs.jsonl")).interrupted == []  # reported once, not on every start
    assert again._next_id == job.id + 1


async def test_a_crash_mid_job_is_also_reported(tmp_path):
    store = JobStore(tmp_path / "jobs.jsonl")
    store.save(Job(id=7, kind="browser", goal="crashed job", status="running"))
    again = JobRunner(JobStore(tmp_path / "jobs.jsonl"))
    assert [j.id for j in again.interrupted] == [7] and again.get(7).status == "interrupted"


async def test_ask_user_raises_a_question_and_marks_the_job(tmp_path):
    r, said, _ = runner(tmp_path)

    async def needs_login(ctx):
        ctx.ask_user("Please log in to SchoolPad in the browser window.")
        return "Stopped at the login page."

    job = r.submit("browser", "check homework", needs_login)
    await r.wait(job.id)
    assert said[0].startswith("Browser job 1 needs you: Please log in")
    assert job.question.startswith("Please log in") and job.status == "done"


async def test_summary_sentences(tmp_path):
    r, _, _ = runner(tmp_path)
    assert r.summary() == "I'm not working on anything in the background."
    job = r.submit("browser", "compare two lamps on the shop", work(delay=1, steps=["reading prices"]))
    await asyncio.sleep(0.05)
    assert r.summary().startswith("browser job 1, compare two lamps on the shop, 0 seconds in: reading prices")
    await r.cancel(job.id)
    assert r.summary().startswith("Nothing running. Last: browser job 1")


async def test_cancel_all_by_kind(tmp_path):
    r, _, _ = runner(tmp_path)
    a = r.submit("browser", "a", work(delay=10))
    b = r.submit("think", "b", work(delay=10))
    await asyncio.sleep(0.02)
    stopped = await r.cancel_all({"browser"})
    assert [j.id for j in stopped] == [a.id] and b.status == "running"
    await r.cancel_all()
    assert b.status == "cancelled"


def test_store_compacts_when_large(tmp_path, monkeypatch):
    import minty_core.jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "STORE_MAX_BYTES", 2000)
    store = JobStore(tmp_path / "jobs.jsonl")
    for i in range(40):
        store.save(Job(id=i % 5, kind="x", goal="g" * 50, status="done"))
    lines = (tmp_path / "jobs.jsonl").read_text().splitlines()
    assert len(lines) <= 10 and {json.loads(l)["id"] for l in lines} == set(range(5))


def test_helpers():
    assert two_sentences("One. Two! Three?") == "One. Two!"
    assert two_sentences("x" * 400).endswith("…") and len(two_sentences("x" * 400)) == 300
    assert short("a  b\n c") == "a b c" and short("y" * 100).endswith("…")


def test_tracer_gets_a_job_event(tmp_path):
    events = []

    class T:
        def event(self, kind, **data):
            events.append((kind, data))

    async def go():
        r = JobRunner(None, tracer=T())
        job = r.submit("think", "q", work())
        await r.wait(job.id)

    asyncio.run(go())
    [(kind, data)] = events
    assert kind == "job" and data["status"] == "done" and data["job_kind"] == "think"


def test_interrupted_notice_wording():
    from minty_core.jobs import interrupted_notice
    one = Job(id=3, kind="browser", goal="compare two lamps")
    assert interrupted_notice([]) == ""
    assert interrupted_notice([one]) == "My earlier browser job 3 to compare two lamps was interrupted when I restarted."
    two = Job(id=4, kind="think", goal="is it safe")
    assert interrupted_notice([one, two]).startswith("2 background jobs were interrupted when I restarted: compare")
