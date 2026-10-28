import asyncio
import json
import sys

import pytest

from minty_core.plugins.builtin.agents.adapters import Invocation, Parsed
from minty_core.plugins.builtin.agents.jobs import JobManager

PASS = [sys.executable, "-c", "print('3 passed in 0.01s')"]
NEEDS_FLAG = [sys.executable, "-c", "import os, sys; print('ok' if os.path.exists('ok.flag') else '1 failed'); "
              "sys.exit(0 if os.path.exists('ok.flag') else 1)"]
# passes on the untouched project, fails once the agent has written a.txt (and passes again if it also writes ok.flag)
BREAKS = [sys.executable, "-c", "import os, sys; bad = os.path.exists('a.txt') and not os.path.exists('ok.flag'); "
          "print('FAILED test_x - assert 1 == 2' if bad else 'ok'); print('1 failed' if bad else '1 passed'); sys.exit(1 if bad else 0)"]
ALWAYS_FAIL = BREAKS


class Coder:
    """Runs the python in the prompt after `Task: `; a resumed session (a second round) runs `fix` instead."""

    name = "fake"
    binary = sys.executable
    readonly_capable = True

    def __init__(self, fix=None):
        self.builds = []
        self.fix = fix

    def build(self, project, prompt, session_id, readonly=False):
        self.builds.append({"prompt": prompt, "session": session_id, "readonly": readonly})
        code = self.fix if (session_id and self.fix) else prompt.split("Task: ", 1)[-1]
        return Invocation([sys.executable, "-c", code], project)

    def parse(self, stdout):
        return Parsed(stdout.strip(), session_id="sess-1")


def make(tmp_path, adapter=None, **kw):
    root = tmp_path / "projects"
    root.mkdir(exist_ok=True)
    (root / "p").mkdir(exist_ok=True)
    heard = []
    adapter = adapter or Coder()
    kw.setdefault("check", PASS)
    mgr = JobManager(root, {"fake": adapter}, heard.append, state={}, log_dir=tmp_path / "logs",
                     sessions_path=tmp_path / "sessions.json", **kw)
    return mgr, heard, adapter, root


async def run(mgr, prompt, **kw):
    job = await mgr.start("fake", "p", prompt, **kw)
    await asyncio.wait_for(mgr._tasks[job.id], 20)
    return job


WRITE = "open('a.txt','w').write('hello'); print('Changed: a.txt')"


async def test_changed_files_and_passing_tests_make_a_verified_job(tmp_path):
    mgr, heard, _, _ = make(tmp_path)
    job = await run(mgr, WRITE)
    assert (job.status, job.verdict, job.rounds) == ("done", "verified", 1)
    assert job.changed == ["a.txt"]
    assert "1 file changed" in heard[0] and "tests passed" in heard[0]


async def test_clean_exit_with_no_changes_is_not_success(tmp_path):
    mgr, heard, _, _ = make(tmp_path)
    job = await run(mgr, "print('All done, everything implemented!')")
    assert (job.status, job.verdict) == ("failed", "no_changes")
    assert "no files changed" in heard[0]


async def test_without_any_tests_the_job_is_done_but_not_confirmed(tmp_path):
    mgr, heard, _, _ = make(tmp_path, check=None)
    job = await run(mgr, WRITE)
    assert (job.status, job.verdict) == ("done", "unchecked")
    assert "could not confirm" in heard[0]


async def test_tests_are_found_from_the_project_when_none_is_configured(tmp_path):
    mgr, _, _, root = make(tmp_path, check=None)
    (root / "p" / "package.json").write_text(json.dumps({"scripts": {"test": "echo no"}}))
    seen = []

    async def fake_check(project, argv, timeout_s=300):
        from minty_core.plugins.builtin.agents.verify import CheckResult
        seen.append(argv)
        return CheckResult(True, True, "ok")

    mgr._run_check = fake_check
    job = await run(mgr, WRITE)
    assert seen == [["npm", "test", "--silent"]] * 2 and job.verdict == "verified"  # once before the agent, once after


async def test_failing_tests_send_the_agent_back_with_the_failure(tmp_path):
    fix = "open('ok.flag','w').write('1'); print('fixed')"
    mgr, heard, adapter, _ = make(tmp_path, adapter=Coder(fix=fix), check=BREAKS)
    job = await run(mgr, WRITE)
    assert (job.status, job.verdict, job.rounds) == ("done", "verified", 2)
    second = adapter.builds[1]
    assert second["session"] == "sess-1" and "1 failed" in second["prompt"] and "checks failed" in second["prompt"].lower()
    assert "2 rounds" in heard[0]


async def test_rounds_are_bounded_and_the_final_report_is_a_failure(tmp_path):
    mgr, heard, adapter, _ = make(tmp_path, adapter=Coder(fix="open('b.txt','w').write('x')"), check=ALWAYS_FAIL,
                                  max_rounds=2)
    job = await run(mgr, WRITE)
    assert (job.status, job.verdict, job.rounds) == ("failed", "checks_failed", 2)
    assert len(adapter.builds) == 2
    assert "tests failed" in heard[0] and "1 failed" in job.checks


async def test_one_round_means_no_retry(tmp_path):
    mgr, _, adapter, _ = make(tmp_path, check=ALWAYS_FAIL, max_rounds=1)
    job = await run(mgr, WRITE)
    assert job.verdict == "checks_failed" and len(adapter.builds) == 1


async def test_an_agent_that_exits_with_an_error_is_failed_even_if_files_changed(tmp_path):
    mgr, _, _, _ = make(tmp_path)
    job = await run(mgr, WRITE + "; import sys; sys.exit(2)")
    assert (job.status, job.verdict) == ("failed", "")


async def test_the_runtime_cap_covers_all_rounds(tmp_path):
    slow = "open('c.txt','w').write('x'); import time; time.sleep(30)"
    mgr, heard, _, _ = make(tmp_path, adapter=Coder(fix=slow), check=ALWAYS_FAIL, max_runtime_s=1.5)
    job = await run(mgr, WRITE)
    assert job.status == "timeout" and "too long" in heard[0]


async def test_a_read_only_question_changes_nothing_and_is_answered(tmp_path):
    mgr, heard, adapter, _ = make(tmp_path)
    job = await run(mgr, "print('The bug is in parse()')", mode="ask")
    assert (job.status, job.verdict) == ("done", "answered")
    assert adapter.builds[0]["readonly"] is True and "The bug is in parse()" in heard[0]


async def test_a_read_only_question_that_wrote_files_is_reported_as_a_problem(tmp_path):
    mgr, heard, _, _ = make(tmp_path)
    job = await run(mgr, WRITE, mode="ask")
    assert (job.status, job.verdict) == ("failed", "changed_in_ask")
    assert "only to look" in heard[0]


async def test_an_agent_that_cannot_be_made_read_only_is_refused_for_questions(tmp_path):
    class Plain(Coder):
        readonly_capable = False

    mgr, _, _, _ = make(tmp_path, adapter=Plain())
    with pytest.raises(ValueError, match="read-only"):
        await mgr.start("fake", "p", "print(1)", mode="ask")
    with pytest.raises(ValueError, match="mode"):
        await mgr.start("fake", "p", "print(1)", mode="yolo")


async def test_what_the_agent_is_doing_can_be_watched_while_it_runs(tmp_path):
    mgr, _, _, _ = make(tmp_path, watch_s=0.1)
    job = await mgr.start("fake", "p", "open('w1.txt','w').write('x'); import time; time.sleep(1.5)")
    for _ in range(40):
        if any("file changed" in line for line in job.progress):
            break
        await asyncio.sleep(0.1)
    assert any("1 file changed" in line for line in job.progress)
    assert "1 file changed" in mgr.status(job.id)
    await mgr.stop(job.id)
    await asyncio.wait_for(mgr._tasks[job.id], 10)


async def test_verification_can_be_switched_off_and_then_says_so(tmp_path):
    mgr, heard, _, _ = make(tmp_path, verify_work=False)
    job = await run(mgr, "print('did it')")
    assert (job.status, job.verdict) == ("done", "unverified")
    assert "not checked" in heard[0]


# ---- jobs survive a restart --------------------------------------------------------------------------------------
async def test_finished_jobs_are_remembered_across_a_restart(tmp_path):
    store = tmp_path / "jobs.json"
    mgr, _, _, _ = make(tmp_path, store_path=store)
    job = await run(mgr, WRITE)
    again, _, _, _ = make(tmp_path, store_path=store)
    assert "(verified)" in again.status(job.id) and again.interrupted == []
    nxt = await run(again, WRITE.replace("a.txt", "b.txt"))
    assert nxt.id == job.id + 1


async def test_a_job_that_was_running_at_a_restart_is_reported_as_interrupted_not_running(tmp_path):
    store = tmp_path / "jobs.json"
    mgr, _, _, _ = make(tmp_path, store_path=store)
    job = await mgr.start("fake", "p", "import time; time.sleep(30)")
    await asyncio.sleep(0.2)
    saved = json.loads(store.read_text())  # what is on disk while it runs: a running job
    assert saved["jobs"][0]["status"] == "running"
    mgr.shutdown()  # the process dies with Minty
    again, _, _, _ = make(tmp_path, store_path=store)
    assert [j.id for j in again.interrupted] == [job.id]
    text = again.status(job.id)
    assert "interrupted" in text and "running" not in text.replace("was running", "")
    notice = again.interrupted_notice()
    assert "interrupted" in notice and "job 1" in notice
    assert again.interrupted_notice() == ""  # told once


async def test_a_corrupt_store_is_ignored(tmp_path):
    store = tmp_path / "jobs.json"
    store.write_text("{not json")
    mgr, _, _, _ = make(tmp_path, store_path=store)
    assert mgr.status() == "No jobs yet."


async def test_waiting_for_a_job_returns_its_checked_status(tmp_path):
    mgr, _, _, _ = make(tmp_path)
    job = await mgr.start("fake", "p", WRITE)
    text = await mgr.wait(job.id)
    assert job.status == "done" and "(verified)" in text and "1 file changed" in text


async def test_cancelling_a_wait_stops_the_agent_it_was_waiting_for(tmp_path):
    mgr, _, _, _ = make(tmp_path)
    job = await mgr.start("fake", "p", "import time; time.sleep(30)")
    waiter = asyncio.create_task(mgr.wait(job.id))
    await asyncio.sleep(0.3)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    await asyncio.wait_for(mgr._tasks[job.id], 10)
    assert job.status == "stopped"


async def test_an_agent_that_is_not_installed_says_so_plainly(tmp_path):
    class Missing(Coder):
        binary = "definitely-not-installed-xyz"

        def build(self, project, prompt, session_id, readonly=False):
            return Invocation(["definitely-not-installed-xyz", "-p", prompt], project)

    mgr, heard, _, _ = make(tmp_path, adapter=Missing())
    job = await run(mgr, WRITE)
    assert job.status == "failed" and "not installed" in job.summary and "Errno" not in job.summary
    assert "not installed" in heard[0]


# ---- tests that were failing before the agent started -----------------------------------------------------------------
BROKEN_ENV = [sys.executable, "-c", "print('ModuleNotFoundError: no module named app'); print('1 failed in 0.02s'); raise SystemExit(1)"]
COUNTS_FILES = [sys.executable, "-c", "import os; n = len([f for f in os.listdir('.') if f.endswith('.txt')]); "
                "print(n, 'failed'); raise SystemExit(1 if n < 9 else 0)"]


async def test_tests_that_were_already_failing_are_not_blamed_on_the_agent(tmp_path):
    mgr, heard, adapter, _ = make(tmp_path, check=BROKEN_ENV)
    job = await run(mgr, WRITE)
    assert (job.status, job.verdict, job.rounds) == ("failed", "already_failing", 1)
    assert len(adapter.builds) == 1  # not sent back to "fix" code that was never broken
    assert "already failing" in heard[0].lower() and job.checks_before


async def test_failing_tests_that_the_agent_fixes_are_verified(tmp_path):
    mgr, _, _, _ = make(tmp_path, check=NEEDS_FLAG)  # fails before: there is no ok.flag yet
    job = await run(mgr, "open('ok.flag','w').write('1'); print('made the tests pass')")
    assert (job.status, job.verdict) == ("done", "verified") and job.checks_before


async def test_tests_that_fail_differently_after_the_change_still_get_a_fix_round(tmp_path):
    fix = "open('b.txt','w').write('y')"
    mgr, _, adapter, _ = make(tmp_path, adapter=Coder(fix=fix), check=COUNTS_FILES)
    job = await run(mgr, WRITE)  # 0 txt files before, 1 after the first round, 2 after the second: never the same output
    assert (job.verdict, job.rounds) == ("checks_failed", 2) and len(adapter.builds) == 2


async def test_the_check_run_before_the_job_does_not_count_as_the_agents_change(tmp_path):
    litter = [sys.executable, "-c", "open('coverage.out','w').write('x'); print('1 passed')"]
    mgr, _, _, _ = make(tmp_path, check=litter)
    job = await run(mgr, "print('did nothing')")
    assert job.verdict == "no_changes" and job.changed == []


async def test_a_projects_own_check_file_is_used_when_no_command_is_set(tmp_path):
    mgr, _, _, root = make(tmp_path, check=None)
    (root / "p" / ".minty").mkdir()
    (root / "p" / ".minty" / "check").write_text(f"{sys.executable} -c \"print('7 passed in 0.1s')\"\n")
    job = await run(mgr, WRITE)
    assert (job.status, job.verdict) == ("done", "verified") and job.checks == "7 passed in 0.1s"
