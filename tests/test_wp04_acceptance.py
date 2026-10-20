"""WP-04 acceptance (plan: kill -9 resumes; approvals bind to the action; exclusive resources and retries are
bounded; a repeated 'send it' sends once). Real stores, a real killed process."""
import asyncio
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

from minty_core.approvals import ApprovalStore
from minty_core.config import SafetyConfig
from minty_core.jobs import JobRunner, make_job_store
from minty_core.policy import R3, Guard

ROOT = Path(__file__).resolve().parents[1]

CHILD = textwrap.dedent("""
    import asyncio, sys
    from pathlib import Path
    from minty_core.jobs import JobRunner, make_job_store

    async def main():
        runner = JobRunner(make_job_store(Path(sys.argv[1]), "sqlite"))

        async def work(ctx):
            ctx.checkpoint({"finished": ["a", "b"]})
            print("ready", flush=True)
            await asyncio.sleep(60)

        runner.submit("research", "do the five things", work, announce=False, resumable=True)
        await asyncio.sleep(60)

    asyncio.run(main())
""")


async def test_kill_nine_during_a_job_is_reported_and_resumable(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", CHILD, str(tmp_path)], stdout=subprocess.PIPE, text=True,
                             env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
    try:
        assert child.stdout.readline().strip() == "ready"
        child.kill()  # SIGKILL: no cleanup of any kind runs
        child.wait(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()
    left = []
    after = JobRunner(make_job_store(tmp_path, "sqlite"))

    async def finish(ctx):
        left.extend(x for x in ["a", "b", "c", "d", "e"] if x not in ctx.resumed_from.get("finished", []))
        return "Done."

    after.register_resumer("research", lambda job: finish)
    job = after.interrupted[0]
    assert job.status == "interrupted" and job.checkpoint == {"finished": ["a", "b"]}
    assert [j.id for j in after.resumable()] == [job.id]
    after.resume(job.id)
    await after.wait(job.id)
    assert after.get(job.id).status == "done" and left == ["c", "d", "e"]


async def test_an_approval_for_one_action_cannot_authorise_another_and_expiry_fails_closed(tmp_path):
    clock = {"now": 5000.0}
    approvals = ApprovalStore(tmp_path / "a.sqlite", clock=lambda: clock["now"], ttl_s=60)
    guard = Guard(SimpleNamespace(config=SimpleNamespace(safety=SafetyConfig())), approvals=approvals)
    a = approvals.request("mail_send", {"to": "teacher@school.example", "body": "I will be late"}, "Send?", risk=R3)
    approvals.decide(a.id, True)
    swapped = guard.use_approval(a.id, "mail_send", {"to": "someone.else@example.com", "body": "I will be late"}, R3)
    assert not swapped.allowed
    assert guard.use_approval(a.id, "mail_send",
                              {"to": "teacher@school.example", "body": "I will be late"}, R3).allowed
    b = approvals.request("mail_send", {"to": "x"}, "Send?", risk=R3)
    approvals.decide(b.id, True)
    clock["now"] += 61
    assert not guard.use_approval(b.id, "mail_send", {"to": "x"}, R3).allowed


async def test_exclusive_jobs_never_overlap_and_retries_stop_at_the_bound(tmp_path):
    runner = JobRunner(make_job_store(tmp_path, "sqlite"), max_concurrent=4, max_attempts=3, retry_delay_s=0)
    live, peak, attempts = 0, 0, []

    async def window_job(ctx):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)
        live -= 1
        return "ok"

    async def always_fails(ctx):
        attempts.append(1)
        raise RuntimeError("down")

    jobs = [runner.submit("browser", f"j{i}", window_job, exclusive="window", announce=False) for i in range(6)]
    bad = runner.submit("research", "bad", always_fails, announce=False)
    for j in (*jobs, bad):
        await runner.wait(j.id)
    assert peak == 1 and all(j.status == "done" for j in jobs)
    assert bad.status == "failed" and len(attempts) == 3
