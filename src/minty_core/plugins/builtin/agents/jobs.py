import asyncio
import atexit
import contextlib
import json
import os
import re
import signal
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from minty_core.tasks import cancel_and_wait

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,60}")


# Wave 16 (D4/C1): the corpus's coding agents all carry rules for scope, reading before editing, tests, git, and a short
# closing report. Minty wraps the user's request in one fixed brief so every coding job behaves the same.
JOB_BRIEF = (
    "You are working for Minty on one project folder. Do only what the task asks, inside this folder. Read a file before "
    "you change it, and run the project's tests after you change code. Never push, force-push, rewrite or delete git "
    "history, delete files you did not create, or touch anything outside this folder. If the task is unclear or risky, "
    "stop and say what you need. Finish with a report of exactly three short lines: Changed: (files and what), "
    "Tests: (what you ran and the result), Left: (anything not done, or nothing).\n\nTask: "
)


def with_brief(prompt: str) -> str:
    return JOB_BRIEF + prompt.strip()


@dataclass
class Job:
    id: int
    agent: str
    project: str
    prompt: str
    status: str = "running"
    summary: str = ""
    started: float = 0.0
    finished: float | None = None


class JobManager:
    def __init__(self, root: Path, adapters: dict, notify: Callable[[str], None], *, state: dict,
                 log_dir: Path, sessions_path: Path, max_concurrent: int = 2, max_runtime_s: float = 1800):
        self._root = Path(root).expanduser()
        resolved = self._root.resolve()
        if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
            raise ValueError(f"refusing to use {resolved} as the project root")
        self._adapters = adapters
        self._notify = notify
        self._log_dir = Path(log_dir)
        self._sessions_path = Path(sessions_path)
        self._max_concurrent = max_concurrent
        self._max_runtime_s = max_runtime_s
        self._jobs: dict[int, Job] = state.setdefault("jobs", {})
        self._tasks: dict[int, asyncio.Task] = state.setdefault("tasks", {})
        self._procs: dict[int, asyncio.subprocess.Process] = state.setdefault("procs", {})
        self._state = state
        if not state.get("atexit"):  # once per process, even across plugin reloads
            state["atexit"] = True
            atexit.register(self.shutdown)

    # -- projects ---------------------------------------------------------
    def project_path(self, name: str) -> Path:
        if not _NAME.fullmatch(name or ""):
            raise ValueError(f"invalid project name {name!r}")
        root = self._root.resolve()
        path = (root / name).resolve()
        if path.parent != root:
            raise ValueError(f"project {name!r} is outside the project root")
        return path

    def list_projects(self) -> list[str]:
        if not self._root.is_dir():
            return []
        return sorted(p.name for p in self._root.iterdir() if p.is_dir() and not p.name.startswith("."))

    def list_agents(self) -> list[str]:
        return [name for name, a in self._adapters.items() if shutil.which(a.binary)]

    # -- sessions ---------------------------------------------------------
    def _sessions(self) -> dict:
        try:
            return json.loads(self._sessions_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_session(self, agent: str, project: str, session_id: str) -> None:
        data = self._sessions()
        data[f"{agent}:{project}"] = session_id
        self._sessions_path.parent.mkdir(parents=True, exist_ok=True)
        self._sessions_path.write_text(json.dumps(data), encoding="utf-8")

    # -- jobs -------------------------------------------------------------
    async def start(self, agent: str, project: str, prompt: str, *, create: bool = False) -> Job:
        adapter = self._adapters.get(agent)
        if adapter is None:
            raise ValueError(f"unknown agent {agent!r}")
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("empty prompt")
        if prompt.startswith("-"):  # never let a prompt be read as a CLI flag
            prompt = "Task: " + prompt
        path = self.project_path(project)
        if not path.is_dir():
            if not create:
                raise ValueError(f"no project named {project!r}")
            path.mkdir(parents=True)
        live = sum(1 for j in self._jobs.values() if j.status == "running" or j.id in self._procs)
        if live >= self._max_concurrent:
            raise RuntimeError(f"{self._max_concurrent} job(s) already running")
        self._state["next_id"] = job_id = self._state.get("next_id", 0) + 1
        job = Job(job_id, agent, project, prompt, started=time.time())
        self._jobs[job_id] = job
        session = self._sessions().get(f"{agent}:{project}")
        invocation = adapter.build(path, with_brief(prompt), session)
        self._tasks[job_id] = asyncio.create_task(self._run(job, adapter, invocation, resumed=bool(session)))
        return job

    @staticmethod
    def _signal_group(proc, sig) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, sig)

    @staticmethod
    async def _exited(proc, timeout: float) -> bool:
        """Poll for the direct child's exit. proc.wait() also waits for stdout to close,
        which grandchildren holding the pipe would block."""
        deadline = time.monotonic() + timeout
        while proc.returncode is None:
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.05)
        return True

    async def _terminate(self, proc) -> None:
        """SIGTERM the whole process group, escalating to SIGKILL after 3 s."""
        self._signal_group(proc, signal.SIGTERM)
        await self._exited(proc, 3)
        self._signal_group(proc, signal.SIGKILL)  # also reaps any grandchildren
        await self._exited(proc, 3)

    async def _run(self, job: Job, adapter, invocation, resumed: bool = False) -> None:
        output = ""
        try:
            if job.status == "stopped":  # stop() ran before the process existed
                return
            proc = await asyncio.create_subprocess_exec(
                *invocation.argv, cwd=invocation.cwd, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            self._procs[job.id] = proc
            if job.status == "stopped":  # stop() landed while the process was spawning
                await self._terminate(proc)
            chunks = bytearray()

            async def read() -> None:
                while data := await proc.stdout.read(65536):
                    chunks.extend(data)

            reader = asyncio.create_task(read())
            try:
                if not await self._exited(proc, self._max_runtime_s):
                    job.status = "timeout"
                    await self._terminate(proc)
                else:
                    # the direct child is done; grandchildren may still hold stdout open
                    try:
                        await asyncio.wait_for(asyncio.shield(reader), 1)
                    except asyncio.TimeoutError:
                        pass
                    self._signal_group(proc, signal.SIGKILL)
            finally:
                await cancel_and_wait(reader)
            output = chunks.decode("utf-8", "replace")
            if job.status in ("stopped", "timeout"):
                pass
            else:
                parsed = adapter.parse(output) if proc.returncode == 0 else None
                if parsed is not None and not parsed.failed:
                    job.status, job.summary = "done", parsed.summary
                    if parsed.session_id:
                        self._save_session(job.agent, job.project, parsed.session_id)
                else:
                    job.status = "failed"
                    job.summary = parsed.summary if parsed else " ".join(output.split())[-300:]
        except Exception as e:  # could not even start (binary missing, bad cwd)
            job.status, job.summary = "failed", f"{type(e).__name__}: {e}"
        finally:
            job.finished = time.time()
            self._procs.pop(job.id, None)
            if job.status == "failed" and resumed:
                self._forget_session(job.agent, job.project)
            self._write_log(job, output)
            self._announce(job)

    def _forget_session(self, agent: str, project: str) -> None:
        data = self._sessions()
        if data.pop(f"{agent}:{project}", None) is not None:
            with contextlib.suppress(OSError):
                self._sessions_path.write_text(json.dumps(data), encoding="utf-8")

    def _write_log(self, job: Job, output: str) -> None:
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            (self._log_dir / f"{job.id}.log").write_text(output, encoding="utf-8")
        except OSError:
            pass

    def _announce(self, job: Job) -> None:
        who = f"{job.agent} finished {job.project}"
        messages = {
            "done": f"{who}: {job.summary[:200]}",
            "failed": f"{job.agent} failed on {job.project}: {job.summary[:200]}",
            "timeout": f"{job.agent} ran too long on {job.project} and was stopped.",
            "stopped": f"Stopped the {job.agent} job on {job.project}.",
        }
        self._notify(messages.get(job.status, who))

    def status(self, job_id: int | None = None) -> str:
        if job_id is not None:
            job = self._jobs.get(job_id)
            return f"No job {job_id}." if job is None else self._describe(job)
        if not self._jobs:
            return "No jobs yet."
        return "\n".join(self._describe(j) for j in list(self._jobs.values())[-5:])

    @staticmethod
    def _describe(job: Job) -> str:
        line = f"Job {job.id} ({job.agent} on {job.project}): {job.status}"
        return line + (f" - {job.summary[:200]}" if job.summary and job.status != "running" else "")

    def shutdown(self) -> None:
        """SIGKILL every live job's process group (jobs run in their own sessions)."""
        for proc in list(self._procs.values()):
            self._signal_group(proc, signal.SIGKILL)

    async def stop(self, job_id: int) -> str:
        job, proc = self._jobs.get(job_id), self._procs.get(job_id)
        if job is None or job.status != "running":
            return f"No job {job_id} is running."
        if proc is not None and proc.returncode is not None:
            return f"Job {job_id} already finished."
        job.status = "stopped"
        if proc is not None:
            self._state.setdefault("killers", set()).add(t := asyncio.create_task(self._terminate(proc)))
            t.add_done_callback(self._state["killers"].discard)
        return f"Stopped job {job_id}."
