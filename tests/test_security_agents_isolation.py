"""A coding agent, and the tests it writes, run inside bubblewrap: the project folder is theirs, the rest of the home folder (Minty's
keys, SSH keys, browser profiles) is not there at all. `auto` uses it whenever it can be built and says so when it cannot;
`required` refuses to run without it; `off` is the user's choice and is reported."""
import asyncio
import shutil
import socket
import sys
import threading

import pytest

from minty_core.sandbox import Sandbox, SandboxUnavailable
from minty_core.plugins.builtin.agents import adapters
from minty_core.plugins.builtin.agents.adapters import Invocation, Parsed
from minty_core.plugins.builtin.agents.isolation import Isolation
from minty_core.plugins.builtin.agents.jobs import JobManager

HAVE_BWRAP = shutil.which("bwrap") is not None and Sandbox().available()[0]
needs_bwrap = pytest.mark.skipif(not HAVE_BWRAP, reason="bubblewrap with user namespaces is not available here")


class FakeBox:
    """Records how it was asked to wrap a command and wraps nothing."""

    def __init__(self, ok=True, why=""):
        self.ok, self.why, self.calls = ok, why, []

    def available(self):
        return self.ok, self.why

    def command(self, argv, *, network=False, ro=(), rw=(), cwd=None, env=None):
        self.calls.append({"argv": list(argv), "network": network, "ro": [str(p) for p in ro], "rw": [str(p) for p in rw],
                           "cwd": cwd, "env": dict(env) if env is not None else None})
        return list(argv)


def test_the_sandbox_can_be_given_the_exact_environment_to_set():
    box = Sandbox(bwrap="bwrap", home=__import__("pathlib").Path("/home/x"))
    argv = box.command(["claude", "-p", "hi"], network=True, env={"HOME": "/home/x", "ANTHROPIC_API_KEY": "k"})
    pairs = [(argv[i + 1], argv[i + 2]) for i, w in enumerate(argv) if w == "--setenv"]
    assert ("HOME", "/home/x") in pairs and ("ANTHROPIC_API_KEY", "k") in pairs and "--clearenv" in argv
    assert argv.index("--clearenv") < argv.index("--setenv") and argv[-3:] == ["claude", "-p", "hi"]
    plain = box.command(["ls"])  # unchanged for every existing caller
    assert ("HOME", "/tmp") in [(plain[i + 1], plain[i + 2]) for i, w in enumerate(plain) if w == "--setenv"]


class Claudeish:
    name = "fake"
    binary = sys.executable
    readonly_capable = False
    sandboxable = True
    home_rw = (".claude", ".claude.json")
    home_ro = (".gitconfig", ".local/bin")

    def build(self, project, prompt, session_id, readonly=False):
        return Invocation([sys.executable, "-c", "print('did it')"], project)

    def parse(self, stdout):
        return Parsed(stdout.strip(), session_id=None)


def make(tmp_path, isolation, adapter=None):
    root = tmp_path / "projects"
    (root / "p").mkdir(parents=True)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude.json").write_text("{}")
    (home / ".gitconfig").write_text("[user]\n")
    mgr = JobManager(root, {"fake": adapter or Claudeish()}, lambda _t: None, state={}, log_dir=tmp_path / "logs",
                     sessions_path=tmp_path / "s.json", check=[sys.executable, "-c", "print('1 passed')"], verify_work=False,
                     isolation=isolation)
    return mgr, root / "p"


async def go(mgr, prompt="do it"):
    job = await mgr.start("fake", "p", prompt)
    await asyncio.wait_for(mgr._tasks[job.id], 20)
    return job


async def test_the_agent_gets_its_project_and_its_own_login_folder_and_nothing_of_the_users(tmp_path):
    box = FakeBox()
    mgr, project = make(tmp_path, Isolation("auto", sandbox=box, home=tmp_path / "home"))
    job = await go(mgr)
    call = box.calls[0]
    assert call["network"] is True and str(project.resolve()) in call["rw"]
    assert str(tmp_path / "home" / ".claude") in call["rw"] and str(tmp_path / "home" / ".claude.json") in call["rw"]
    assert str(tmp_path / "home" / ".gitconfig") in call["ro"] and str(tmp_path / "home" / ".local/bin") not in call["ro"]  # absent: skipped
    assert not any(".config" in p or ".ssh" in p for p in call["rw"] + call["ro"])
    assert call["env"] is not None and "HOME" in call["env"] and call["cwd"] == project.resolve()
    assert job.sandboxed is True and job.status == "done"
    assert "sandbox" in job.summary.lower() and "out of reach" in job.summary


async def test_the_projects_own_tests_run_in_the_sandbox_with_no_network(tmp_path):
    box = FakeBox()
    mgr, project = make(tmp_path, Isolation("auto", sandbox=box, home=tmp_path / "home"))
    mgr._verify = True
    job = await go(mgr)
    checks = [c for c in box.calls if c["argv"][:1] == [sys.executable] and "1 passed" in " ".join(c["argv"])]
    assert checks, box.calls
    assert all(c["network"] is False and c["rw"] == [str(project.resolve())] for c in checks)


async def test_auto_runs_unsandboxed_when_it_must_and_says_so(tmp_path):
    mgr, _ = make(tmp_path, Isolation("auto", sandbox=FakeBox(False, "bubblewrap (bwrap) is not installed"), home=tmp_path / "home"))
    job = await go(mgr)
    assert job.status == "done" and job.sandboxed is False
    assert "not sandboxed" in job.summary.lower() and "bubblewrap" in job.summary


async def test_required_refuses_to_run_without_the_sandbox(tmp_path):
    mgr, project = make(tmp_path, Isolation("required", sandbox=FakeBox(False, "user namespaces are not available"), home=tmp_path / "home"))
    job = await go(mgr)
    assert job.status == "failed" and "sandbox" in job.summary.lower() and "not run" in job.summary.lower()
    assert list(project.iterdir()) == []


async def test_off_is_reported_not_hidden(tmp_path):
    box = FakeBox()
    mgr, _ = make(tmp_path, Isolation("off", sandbox=box, home=tmp_path / "home"))
    job = await go(mgr)
    assert box.calls == [] and job.sandboxed is False and "not sandboxed" in job.summary.lower()


async def test_an_adapter_that_cannot_be_sandboxed_is_run_as_before(tmp_path):
    box = FakeBox()

    class Plain(Claudeish):
        sandboxable = False

    mgr, _ = make(tmp_path, Isolation("auto", sandbox=box, home=tmp_path / "home"), adapter=Plain())
    job = await go(mgr)
    assert box.calls == [] and job.status == "done"


def test_an_unknown_mode_means_auto():
    assert Isolation("whatever", sandbox=FakeBox()).mode == "auto"


# ---- against the real bubblewrap ------------------------------------------------------------------------------------
@pytest.fixture
def world(tmp_path):
    home = tmp_path / "home"
    (home / ".config" / "minty").mkdir(parents=True)
    (home / ".config" / "minty" / "secrets.toml").write_text("groq = 'CANARY-SECRET'\n")
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_ed25519").write_text("CANARY-KEY")
    (home / ".claude").mkdir()
    (home / ".claude" / "state").write_text("mine")
    project = tmp_path / "proj"
    project.mkdir()
    return home, project


PROBE = (
    "import os, json, pathlib, socket, sys\n"
    "home = pathlib.Path(os.environ['HOME'])\n"
    "out = {}\n"
    "out['secrets'] = (home / '.config/minty/secrets.toml').exists()\n"
    "out['ssh'] = (home / '.ssh').exists()\n"
    "out['claude_state'] = (home / '.claude/state').read_text() if (home / '.claude/state').exists() else None\n"
    "pathlib.Path('made.txt').write_text('ok'); out['wrote'] = pathlib.Path('made.txt').exists()\n"
    "out['env_keys'] = sorted(k for k in os.environ if 'KEY' in k or 'TOKEN' in k)\n"
    "try:\n"
    "    socket.create_connection(('127.0.0.1', int(sys.argv[1])), timeout=2).close(); out['net'] = True\n"
    "except OSError:\n"
    "    out['net'] = False\n"
    "print(json.dumps(out))\n"
)


@pytest.fixture
def listener():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    stop = threading.Event()

    def accept():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                srv.accept()[0].close()
            except OSError:
                pass

    t = threading.Thread(target=accept, daemon=True)
    t.start()
    yield srv.getsockname()[1]
    stop.set()
    t.join(2)
    srv.close()


async def run_wrapped(argv):
    proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(), 30)
    assert proc.returncode == 0, err.decode()
    import json
    return json.loads(out.decode())


@needs_bwrap
async def test_the_real_sandbox_hides_the_users_keys_and_keeps_the_agents_own_folder(world, listener):
    home, project = world
    iso = Isolation("required", home=home)
    env = adapters.job_env({"PATH": "/usr/bin:/bin", "HOME": str(home), "GROQ_API_KEY": "leak", "ANTHROPIC_API_KEY": "mine"})
    argv = iso.agent_command([sys.executable, "-c", PROBE, str(listener)], project, Claudeish(), env)
    got = await run_wrapped(argv)
    assert got["secrets"] is False and got["ssh"] is False
    assert got["claude_state"] == "mine" and got["wrote"] is True
    assert got["env_keys"] == ["ANTHROPIC_API_KEY"] and got["net"] is True
    assert (project / "made.txt").read_text() == "ok"


@needs_bwrap
async def test_the_real_sandbox_for_the_tests_the_agent_wrote_has_no_network(world, listener):
    home, project = world
    iso = Isolation("required", home=home)
    got = await run_wrapped(iso.check_command([sys.executable, "-c", PROBE, str(listener)], project))
    assert got["secrets"] is False and got["ssh"] is False and got["net"] is False and got["wrote"] is True


def test_required_raises_without_a_sandbox(tmp_path):
    with pytest.raises(SandboxUnavailable):
        Isolation("required", sandbox=FakeBox(False, "nope"), home=tmp_path).prepare()


class BrokenCheckBox(FakeBox):
    """A sandbox in which the project's test program is not there (it lives in a folder the sandbox hides)."""

    def command(self, argv, *, network=False, **kw):
        super().command(argv, network=network, **kw)
        if network:
            return list(argv)
        return [sys.executable, "-c", "import sys; print('bwrap: execvp pytest: No such file or directory'); sys.exit(1)"]


class Writer(Claudeish):
    def build(self, project, prompt, session_id, readonly=False):
        return Invocation([sys.executable, "-c", "open('a.txt', 'w').write('hi'); print('Changed: a.txt')"], project)


async def test_tests_that_cannot_start_in_the_sandbox_are_not_counted_as_failing_tests(tmp_path):
    box = BrokenCheckBox()
    mgr, project = make(tmp_path, Isolation("auto", sandbox=box, home=tmp_path / "home"), adapter=Writer())
    mgr._verify = True
    job = await go(mgr)
    assert job.verdict == "unchecked" and job.status == "done" and job.rounds == 1, (job.verdict, job.summary)
    assert "could not start inside the sandbox" in job.summary and "pytest" in job.summary
    assert job.changed == ["a.txt"]
