import asyncio
import sys

import pytest

from minty_core.plugins.builtin.agents.adapters import Invocation, Parsed
from minty_core.plugins.builtin.agents.jobs import JobManager


class ScriptAdapter:
    """Runs `python -c <script>`; the prompt is passed as argv[1] so tests control the child."""

    name = "fake"
    binary = sys.executable

    def __init__(self):
        self.builds = []

    def build(self, project, prompt, session_id):
        self.builds.append((project, prompt, session_id))
        return Invocation([sys.executable, "-c", prompt.split("Task: ", 1)[-1]], project)  # the job brief comes first

    def parse(self, stdout):
        return Parsed(stdout.strip(), session_id="sess-1")


def make(tmp_path, notify=None, **kw):
    root = tmp_path / "projects"
    root.mkdir()
    heard = [] if notify is None else notify
    adapter = ScriptAdapter()
    mgr = JobManager(root, {"fake": adapter}, heard.append, state={}, log_dir=tmp_path / "logs",
                     sessions_path=tmp_path / "sessions.json", **kw)
    return mgr, heard, adapter, root


async def settle(mgr, job, timeout=5):
    await asyncio.wait_for(mgr._tasks[job.id], timeout)


async def test_job_runs_in_project_dir_notifies_and_logs(tmp_path):
    mgr, heard, adapter, root = make(tmp_path)
    (root / "notes").mkdir()
    job = await mgr.start("fake", "notes", "import os; print('built in', os.path.basename(os.getcwd()))")
    assert job.status == "running"
    await settle(mgr, job)
    assert job.status == "done" and job.summary == "built in notes"
    assert heard == ["fake finished notes: built in notes"]
    assert (tmp_path / "logs" / f"{job.id}.log").read_text().strip() == "built in notes"
    assert "done" in mgr.status(job.id)


async def test_failure_is_reported(tmp_path):
    mgr, heard, _, root = make(tmp_path)
    (root / "p").mkdir()
    job = await mgr.start("fake", "p", "import sys; print('oops'); sys.exit(3)")
    await settle(mgr, job)
    assert job.status == "failed" and "failed" in heard[0] and "oops" in heard[0]


async def test_session_id_is_saved_and_resumed(tmp_path):
    mgr, _, adapter, root = make(tmp_path)
    (root / "p").mkdir()
    await settle(mgr, await mgr.start("fake", "p", "print('a')"))
    await settle(mgr, await mgr.start("fake", "p", "print('b')"))
    assert [b[2] for b in adapter.builds] == [None, "sess-1"]


async def test_create_makes_the_project_folder(tmp_path):
    mgr, _, _, root = make(tmp_path)
    with pytest.raises(ValueError):
        await mgr.start("fake", "brand-new", "print(1)")
    job = await mgr.start("fake", "brand-new", "print(1)", create=True)
    await settle(mgr, job)
    assert (root / "brand-new").is_dir() and mgr.list_projects() == ["brand-new"]


@pytest.mark.parametrize("name", ["../escape", "a/b", "..", ".hidden", "", "x" * 80, "/etc", "ok\n"])
async def test_project_names_cannot_leave_the_root(tmp_path, name):  # RF7
    mgr, _, _, root = make(tmp_path)
    with pytest.raises(ValueError):
        mgr.project_path(name)
    with pytest.raises(ValueError):
        await mgr.start("fake", name, "print(1)", create=True)
    assert list(tmp_path.glob("escape*")) == []


async def test_symlink_out_of_root_is_rejected(tmp_path):  # RF7
    mgr, _, _, root = make(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "sneaky").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        mgr.project_path("sneaky")


async def test_concurrency_cap_and_unknown_agent(tmp_path):
    mgr, _, _, root = make(tmp_path, max_concurrent=1)
    (root / "p").mkdir()
    first = await mgr.start("fake", "p", "import time; time.sleep(5)")
    with pytest.raises(RuntimeError, match="already running"):
        await mgr.start("fake", "p", "print(1)")
    with pytest.raises(ValueError):
        await mgr.start("nope", "p", "print(1)")
    await mgr.stop(first.id)
    await settle(mgr, first)


async def test_runtime_cap_kills_the_job(tmp_path):
    mgr, heard, _, root = make(tmp_path, max_runtime_s=0.3)
    (root / "p").mkdir()
    job = await mgr.start("fake", "p", "import time; time.sleep(30)")
    await settle(mgr, job)
    assert job.status == "timeout" and "too long" in heard[0]


async def test_stop_terminates_the_job(tmp_path):
    mgr, heard, _, root = make(tmp_path)
    (root / "p").mkdir()
    job = await mgr.start("fake", "p", "import time; time.sleep(30)")
    assert "Stopped" in await mgr.stop(job.id)
    await settle(mgr, job)
    assert job.status == "stopped"
    assert "No job" in await mgr.stop(999)


async def test_prompts_cannot_masquerade_as_flags(tmp_path):
    mgr, _, adapter, root = make(tmp_path)
    (root / "p").mkdir()
    job = await mgr.start("fake", "p", "  --dangerously-skip-permissions")
    await settle(mgr, job)
    sent = adapter.builds[0][1]  # the fixed brief comes first, so it can never be read as a flag
    assert sent.endswith("Task: --dangerously-skip-permissions") and not sent.startswith("-")
    with pytest.raises(ValueError):
        await mgr.start("fake", "p", "   ")


@pytest.mark.parametrize("agent", ["hermes", "openclaw"])
@pytest.mark.parametrize("prompt", ["--dangerously-skip-permissions", "-p evil"])
async def test_hostile_prompts_never_start_with_a_dash_in_real_adapter_argv(tmp_path, monkeypatch, agent, prompt):
    from minty_core.plugins.builtin.agents.adapters import HermesAdapter, OpenClawAdapter
    seen = []

    async def fake_exec(*argv, **kwargs):  # records argv; never starts anything
        seen.append(list(argv))
        raise FileNotFoundError("fake")

    monkeypatch.setattr("minty_core.plugins.builtin.agents.jobs.asyncio.create_subprocess_exec", fake_exec)
    root = tmp_path / "projects"
    (root / "p").mkdir(parents=True)
    adapters = {"hermes": HermesAdapter(), "openclaw": OpenClawAdapter()}
    mgr = JobManager(root, adapters, [].append, state={}, log_dir=tmp_path / "logs",
                     sessions_path=tmp_path / "sessions.json")
    job = await mgr.start(agent, "p", prompt)
    await settle(mgr, job)
    assert len(seen) == 1
    carrying = [a for a in seen[0] if prompt.strip() in a]
    assert len(carrying) == 1 and carrying[0].endswith("Task: " + prompt) and not carrying[0].startswith("-")
    assert not any(a.startswith("-") and a not in ("-z", "--agent", "--message", "--json") for a in seen[0])


async def test_status_overview(tmp_path):
    mgr, _, _, root = make(tmp_path)
    assert mgr.status() == "No jobs yet."
    (root / "p").mkdir()
    job = await mgr.start("fake", "p", "print('x')")
    await settle(mgr, job)
    assert f"Job {job.id}" in mgr.status() and "No job" in mgr.status(404)


GRANDCHILD = (
    "import subprocess, sys\n"
    "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
    "print('done'); print(g.pid)\n"
)


def alive(pid):
    import os
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def test_grandchildren_do_not_hang_the_job_and_are_killed(tmp_path):
    import time
    mgr, heard, _, root = make(tmp_path)
    (root / "p").mkdir()
    t0 = time.monotonic()
    job = await mgr.start("fake", "p", GRANDCHILD)
    await settle(mgr, job)
    assert time.monotonic() - t0 < 5
    assert job.status == "done" and job.summary.startswith("done")
    pid = int((tmp_path / "logs" / f"{job.id}.log").read_text().split()[-1])
    for _ in range(20):
        if not alive(pid):
            break
        await asyncio.sleep(0.1)
    assert not alive(pid)


async def test_stop_before_the_process_exists_prevents_the_exec(tmp_path):
    mgr, heard, adapter, root = make(tmp_path)
    (root / "p").mkdir()
    marker = tmp_path / "marker"
    job = await mgr.start("fake", "p", f"open({str(marker)!r}, 'w').close()")
    await mgr.stop(job.id)
    await settle(mgr, job)
    await asyncio.sleep(0.3)
    assert job.status == "stopped" and not marker.exists()


async def test_cap_counts_jobs_whose_process_is_still_alive(tmp_path):
    mgr, _, _, root = make(tmp_path, max_concurrent=1)
    (root / "p").mkdir()
    ready = tmp_path / "ready"
    script = (f"import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
              f"open({str(ready)!r}, 'w').close()\ntime.sleep(30)")
    first = await mgr.start("fake", "p", script)
    for _ in range(50):
        if ready.exists():
            break
        await asyncio.sleep(0.1)
    await mgr.stop(first.id)
    with pytest.raises(RuntimeError, match="already running"):
        await mgr.start("fake", "p", "print(1)")
    await settle(mgr, first, timeout=8)  # SIGKILL escalation
    assert first.status == "stopped"
    await settle(mgr, await mgr.start("fake", "p", "print(1)"))


async def test_failed_resumed_session_is_forgotten(tmp_path):
    import json
    mgr, _, _, root = make(tmp_path)
    (root / "p").mkdir()
    sessions = tmp_path / "sessions.json"
    sessions.write_text(json.dumps({"fake:p": "dead", "fake:other": "keep"}))
    job = await mgr.start("fake", "p", "import sys; sys.exit(1)")
    await settle(mgr, job)
    assert job.status == "failed"
    assert json.loads(sessions.read_text()) == {"fake:other": "keep"}


async def test_parsed_failure_marks_the_job_failed(tmp_path):
    mgr, heard, adapter, root = make(tmp_path)
    (root / "p").mkdir()
    adapter.parse = lambda out: Parsed("model said no", "s", failed=True)
    job = await mgr.start("fake", "p", "print('x')")
    await settle(mgr, job)
    assert job.status == "failed" and "failed" in heard[0] and "model said no" in heard[0]


def test_dangerous_roots_are_refused(tmp_path):
    from pathlib import Path
    for bad in (Path("/"), Path.home()):
        with pytest.raises(ValueError):
            JobManager(bad, {}, print, state={}, log_dir=tmp_path, sessions_path=tmp_path / "s.json")


async def test_create_makes_missing_root(tmp_path):
    mgr, _, _, root = make(tmp_path)
    root.rmdir()
    root_deep = tmp_path / "a" / "b"
    mgr2 = JobManager(root_deep, {"fake": ScriptAdapter()}, print, state={}, log_dir=tmp_path / "l",
                      sessions_path=tmp_path / "s2.json")
    await settle(mgr2, await mgr2.start("fake", "new", "print(1)", create=True))
    assert (root_deep / "new").is_dir()


def pid_script(pidfile):
    return f"import os, time\nopen({str(pidfile)!r}, 'w').write(str(os.getpid()))\ntime.sleep(30)"


async def wait_dead(pid, tries=60):
    for _ in range(tries):
        if not alive(pid):
            return True
        await asyncio.sleep(0.1)
    return False


async def test_stop_during_spawn_still_kills_the_job(tmp_path):
    import time
    mgr, _, _, root = make(tmp_path)
    (root / "p").mkdir()
    pidfile = tmp_path / "pid"
    t0 = time.monotonic()
    job = await mgr.start("fake", "p", pid_script(pidfile))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await mgr.stop(job.id)
    await settle(mgr, job, timeout=6)
    assert time.monotonic() - t0 < 5 and job.status == "stopped"
    if pidfile.exists():
        assert await wait_dead(int(pidfile.read_text()))


async def test_shutdown_kills_live_process_groups(tmp_path):
    mgr, _, _, root = make(tmp_path)
    (root / "p").mkdir()
    pidfile = tmp_path / "pid"
    job = await mgr.start("fake", "p", pid_script(pidfile))
    for _ in range(50):
        if pidfile.exists() and pidfile.read_text():
            break
        await asyncio.sleep(0.1)
    pid = int(pidfile.read_text())
    mgr.shutdown()
    assert await wait_dead(pid)
    await settle(mgr, job)


async def test_stop_after_the_child_exited_does_not_relabel(tmp_path):
    mgr, _, _, root = make(tmp_path)
    (root / "p").mkdir()
    job = await mgr.start("fake", "p", GRANDCHILD)  # grandchild keeps stdout open briefly
    for _ in range(200):
        proc = mgr._procs.get(job.id)
        if proc is not None and proc.returncode is not None:
            break
        await asyncio.sleep(0.01)
    assert "already finished" in await mgr.stop(job.id)
    await settle(mgr, job)
    assert job.status == "done"
