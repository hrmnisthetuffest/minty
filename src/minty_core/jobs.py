"""Background jobs: long work (browser tasks, helpers, deep thinking, coding agents) runs here, so the voice
conversation never waits for it.

Each job has a short speakable id ("job 3"), a status, a progress tail, a time budget and an optional exclusive
resource (two browser jobs never drive the same window at once). State is appended to a JSONL file so a restart
reports what was interrupted instead of forgetting it. Finished jobs announce themselves through `notify`.
"""
import asyncio
import contextlib
import json
import logging
import re
import shutil
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, NamedTuple

from minty_core.origin import current_origin, set_origin

log = logging.getLogger(__name__)

FINAL = frozenset({"done", "failed", "cancelled", "timeout", "interrupted", "blocked"})
OUTCOMES = frozenset({"done", "failed", "blocked"})  # the statuses a work function may end the job with
OFFERED = frozenset({"interrupted", "blocked"})  # resumable jobs Minty offers by itself (a failed one is only retried on request)
RESUME_FROM = OFFERED | {"failed", "timeout"}
ACTIVE = frozenset({"queued", "running", "needs_user", "waiting_approval"})
MAX_BACKOFF_S = 60.0
PROGRESS_KEEP = 20
STORE_MAX_BYTES = 1_000_000
KIND_LABEL = {"mission": "mission", "browser": "browser job", "subagent": "helper", "think": "deep thinking", "coding": "coding job",
              "research": "research"}
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def two_sentences(text: str, limit: int = 300) -> str:
    parts = _SENTENCE.split(" ".join((text or "").split()))
    out = " ".join(parts[:2]).strip()
    return out if len(out) <= limit else out[: limit - 1].rstrip() + "…"


def short(goal: str, limit: int = 60) -> str:
    goal = " ".join((goal or "").split())
    return goal if len(goal) <= limit else goal[: limit - 1].rstrip() + "…"


class JobOutcome(NamedTuple):
    """What a work function returns when "it returned" is not the whole story: failed (with its own reason), or blocked
    (it needs the user's answer). A plain string still means done."""
    status: str
    text: str


@dataclass
class Job:
    id: int
    kind: str
    goal: str
    status: str = "queued"
    created: float = 0.0
    started: float | None = None
    finished: float | None = None
    result: str = ""
    progress: list[str] = field(default_factory=list)
    question: str = ""
    budget_s: float = 600.0
    private: bool = False
    exclusive: str | None = None
    artifacts: list[str] = field(default_factory=list)
    reported: bool = True  # False for an interruption the user has not been told about yet
    attempts: int = 0  # how many times the work function has been started
    max_attempts: int = 1  # bounded retries after an error (timeouts and cancellations are never retried)
    approval_id: int | None = None  # the approval this job is waiting on (status waiting_approval)
    checkpoint: dict = field(default_factory=dict)  # what the work saved so a resume can carry on from there
    resumable: bool = False  # the work can be picked up again from its checkpoint after an interruption
    payload: dict = field(default_factory=dict)  # inputs a resumer needs to rebuild the work
    resumed: int = 0  # how many times it was resumed
    origin: str = "laptop"  # where the request came from ("telegram": its questions go to the phone and nowhere else)

    @property
    def active(self) -> bool:
        return self.status in ACTIVE

    def label(self) -> str:
        return f"{KIND_LABEL.get(self.kind, self.kind)} {self.id}"

    def elapsed(self, now: float) -> float:
        return (self.finished or now) - (self.started or self.created)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def interrupted_notice(jobs: list["Job"], resumable_ids=()) -> str:
    """What to say after a restart about work that was cut short. It is never re-run automatically; a job that can
    be picked up again says how."""
    if not jobs:
        return ""
    can = [j for j in jobs if j.id in set(resumable_ids)]
    if len(jobs) == 1:
        text = f"My earlier {jobs[0].label()} to {short(jobs[0].goal, 60)} was interrupted when I restarted."
        return text + (f" Say resume job {jobs[0].id} to carry on." if can else "")
    names = ", ".join(short(j.goal, 40) for j in jobs[:3])
    text = f"{len(jobs)} background jobs were interrupted when I restarted: {names}."
    return text + (" I can resume some of them; ask what I can resume." if can else "")


class JobStore:
    """Append-only JSONL; the last line for an id wins. Compacted when it grows past STORE_MAX_BYTES."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.Lock()
        self.interrupted: list[Job] = []  # jobs that were still running when the program last stopped

    def save(self, job: Job) -> None:
        line = json.dumps(job.to_dict(), ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            try:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                if self.path.stat().st_size > STORE_MAX_BYTES:
                    self._compact()
            except OSError as e:
                log.warning("could not save job %s: %s", job.id, e)

    def _compact(self) -> None:
        latest = self._read()
        keep = sorted(latest.values(), key=lambda j: j.id)[-200:]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(j.to_dict(), ensure_ascii=False) + "\n" for j in keep), encoding="utf-8")
        tmp.replace(self.path)

    def _read(self) -> dict[int, Job]:
        jobs: dict[int, Job] = {}
        if not self.path.exists():
            return jobs
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                job = Job.from_dict(json.loads(line))
            except (json.JSONDecodeError, TypeError):
                continue
            jobs[job.id] = job
        return jobs

    def load(self) -> dict[int, Job]:
        jobs = self._read()
        self.interrupted = []
        for job in jobs.values():
            if job.status in ACTIVE:  # the program died mid-job
                job.status, job.finished, job.reported = "interrupted", self._clock(), False
                job.result = job.result or "Interrupted when Minty restarted."
            if job.status == "interrupted" and not job.reported:
                job.reported = True
                self.interrupted.append(job)
                self.save(job)
        return jobs


class SqliteJobStore(JobStore):
    """The same interface on a SQLite (WAL) database: one row per job, the last save wins, finished jobs are pruned
    in batches, active ones never. Crash-safe where a half-written JSONL line was only skipped."""

    PRUNE_BATCH = 64

    def __init__(self, path: Path, clock: Callable[[], float] = time.time, keep_finished: int = 500):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock, self._keep = clock, int(keep_finished)
        self._lock = threading.Lock()
        self.interrupted: list[Job] = []
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, status TEXT NOT NULL,"
                         " data TEXT NOT NULL, updated REAL NOT NULL)")
        self._db.commit()

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    def save(self, job: Job) -> None:
        line = json.dumps(job.to_dict(), ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            try:
                self._db.execute("INSERT OR REPLACE INTO jobs(id, status, data, updated) VALUES (?,?,?,?)",
                                 (job.id, job.status, line, self._clock()))
                if job.status in FINAL:
                    self._prune()
                self._db.commit()
            except sqlite3.Error as e:
                log.warning("could not save job %s: %s", job.id, e)

    def _prune(self) -> None:
        marks = ",".join("?" for _ in FINAL)
        n = self._db.execute(f"SELECT COUNT(*) FROM jobs WHERE status IN ({marks})", tuple(FINAL)).fetchone()[0]
        if n > self._keep + self.PRUNE_BATCH:
            self._db.execute(
                f"DELETE FROM jobs WHERE id IN (SELECT id FROM jobs WHERE status IN ({marks}) ORDER BY id LIMIT ?)",
                (*FINAL, n - self._keep))

    def _read(self) -> dict[int, Job]:
        jobs: dict[int, Job] = {}
        with self._lock:
            rows = self._db.execute("SELECT data FROM jobs ORDER BY id").fetchall()
        for (data,) in rows:
            try:
                job = Job.from_dict(json.loads(data))
            except (json.JSONDecodeError, TypeError):
                continue
            jobs[job.id] = job
        return jobs


def make_job_store(directory: Path, backend: str = "sqlite", clock: Callable[[], float] = time.time) -> JobStore:
    """backend "jsonl" is the original file. "sqlite": jobs.sqlite, filled once from jobs.jsonl after a dated backup
    (the old file stays, so the setting can go back)."""
    directory = Path(directory)
    legacy = directory / "jobs.jsonl"
    if backend != "sqlite":
        return JobStore(legacy, clock)
    store = SqliteJobStore(directory / "jobs.sqlite", clock)
    if store.count() == 0 and legacy.exists():
        try:
            shutil.copy2(legacy, legacy.with_name(f"jobs.jsonl.bak-{time.strftime('%Y%m%d-%H%M%S')}"))
            for old in sorted(JobStore(legacy, clock)._read().values(), key=lambda j: j.id):
                store.save(old)
        except OSError:
            log.warning("could not import the old jobs file", exc_info=True)
    return store


class JobContext:
    """What a job's work function gets: report progress, check for cancellation, raise a question."""

    def __init__(self, runner: "JobRunner", job: Job):
        self._runner, self.job = runner, job
        self.last_approval = ""  # how the last question ended: approved, denied or expired

    @property
    def waited_s(self) -> float:
        """Seconds this job has spent waiting for the user's answer so far: the time budget does not count them."""
        return self._runner.waited_s(self.job.id)

    def progress(self, text: str) -> None:
        self.job.progress = (self.job.progress + [short(text, 160)])[-PROGRESS_KEEP:]
        self._runner._publish(self.job)

    @property
    def cancelled(self) -> bool:
        return self.job.status == "cancelled"

    def ask_user(self, question: str) -> None:
        """Background work cannot block on a person: the question is raised and the job carries on (or stops)."""
        self.job.question, self.job.status = question, "needs_user"
        self._runner._persist(self.job)
        self._runner._say(f"{self.job.label().capitalize()} needs you: {question}")

    def artifact(self, path: str) -> None:
        self.job.artifacts.append(str(path))

    def checkpoint(self, data: dict) -> None:
        """Save what is done so far (JSON-able). After a restart a resume gets it back as `resumed_from`."""
        self.job.checkpoint = {**self.job.checkpoint, **data}
        self._runner._persist(self.job)

    @property
    def resumed_from(self) -> dict:
        """The checkpoint this run continues from; empty on a first run."""
        return dict(self.job.checkpoint) if self.job.resumed else {}

    async def wait_on_open_approval(self, question: str, *, poll_s: float = 0.5, ttl_s: float = 6 * 3600.0) -> bool | None:
        """Wait for the user's answer to a question the safety gate has already put to them, instead of asking a second time.
        True for a yes, False for a no or an expiry, None when the gate opened no such question. The gate itself uses up the
        yes, so this only watches it."""
        approvals = self._runner._approvals
        record = approvals.find_pending(question) if approvals is not None else None
        if record is None:
            return None
        approvals.attach_job(record.id, self.job.id)
        approvals.extend(record.id, ttl_s)
        self.last_approval = ""
        self.job.status, self.job.approval_id, self.job.question = "waiting_approval", record.id, question
        self._runner._persist(self.job)
        self._runner._say(f"{self.job.label().capitalize()} needs your approval: {question}")
        self._runner._pause(self.job.id)
        try:
            while True:
                current = approvals.get(record.id)
                if current is None or current.status not in ("pending", "approved"):
                    self.last_approval = "expired" if current is not None and current.status == "expired" else "denied"
                    return False
                if current.status == "approved":
                    self.last_approval = "approved"
                    return True
                await asyncio.sleep(poll_s)
        finally:
            self._runner._resume(self.job.id)
            if self.job.status == "waiting_approval":
                self.job.status, self.job.question = "running", ""
                self._runner._persist(self.job)

    async def ask_approval(self, name: str, args: dict, question: str, *, risk: int = 3, ttl_s: float | None = None,
                           poll_s: float = 0.5) -> bool:
        """Park this job until the user decides. True only for a yes that covers exactly this action; a no, an
        expiry or a missing approval store is a no (fail closed). The job's own time budget keeps running."""
        approvals = self._runner._approvals
        if approvals is None:
            return False
        record = approvals.request(name, args, question, risk, ttl_s=ttl_s, job_id=self.job.id)
        self.job.status, self.job.approval_id, self.job.question = "waiting_approval", record.id, question
        self._runner._persist(self.job)
        self._runner._say(f"{self.job.label().capitalize()} needs your approval: {question}")
        self._runner._pause(self.job.id)
        try:
            while True:
                current = approvals.get(record.id)
                if current is None or current.status not in ("pending", "approved"):
                    return False
                if current.status == "approved":
                    return approvals.consume(record.id, name, args)
                await asyncio.sleep(poll_s)
        finally:
            self._runner._resume(self.job.id)
            if self.job.status == "waiting_approval":
                self.job.status, self.job.question = "running", ""
                self._runner._persist(self.job)


class JobRunner:
    def __init__(self, store: JobStore | None = None, *, bus=None, tracer=None,
                 notify: Callable[[str], None] | None = None, max_concurrent: int = 3,
                 clock: Callable[[], float] = time.time, approvals=None, max_attempts: int = 1,
                 retry_delay_s: float = 5.0, resume: bool = True):
        self._store, self._bus, self._tracer = store, bus, tracer
        self._approvals = approvals  # ApprovalStore, for jobs that must wait for a yes
        self._resumers: dict[str, Callable[[Job], Callable]] = {}  # kind -> (job) -> the work function
        self._resume_enabled = bool(resume)
        self._max_attempts, self._retry_delay = max(1, int(max_attempts)), max(0.0, float(retry_delay_s))
        self._notify = notify
        self._clock = clock
        self.jobs: dict[int, Job] = store.load() if store is not None else {}
        self.interrupted: list[Job] = list(store.interrupted) if store is not None else []
        self._next_id = max(self.jobs, default=0) + 1
        self._max = max(1, int(max_concurrent))
        self._tasks: dict[int, asyncio.Task] = {}
        self._slots: asyncio.Semaphore | None = None
        self._locks: dict[str, asyncio.Lock] = {}
        self._waited: dict[int, float] = {}  # job -> seconds spent waiting for the user (finished waits)
        self._waiting_since: dict[int, float] = {}  # job -> when the wait it is in now began
        self.on_finish: list[Callable[[Job], None]] = []  # called once a job reaches a final state

    # ---- the time budget counts work, not waiting for the user ------------------------------------
    def _pause(self, job_id: int) -> None:
        self._waiting_since.setdefault(job_id, time.monotonic())

    def _resume(self, job_id: int) -> None:
        began = self._waiting_since.pop(job_id, None)
        if began is not None:
            self._waited[job_id] = self._waited.get(job_id, 0.0) + (time.monotonic() - began)

    def waited_s(self, job_id: int) -> float:
        began = self._waiting_since.get(job_id)
        return self._waited.get(job_id, 0.0) + (time.monotonic() - began if began is not None else 0.0)

    async def _within_budget(self, job: Job, work: Awaitable):
        """Run the work for at most `job.budget_s` seconds of working: the time it waits for the user to answer a question is not counted,
        so a job that asked something and was not answered for an hour has not used up its budget."""
        task = asyncio.ensure_future(work)
        began, waited0 = time.monotonic(), self.waited_s(job.id)
        try:
            while True:
                left = job.budget_s - ((time.monotonic() - began) - (self.waited_s(job.id) - waited0))
                if left <= 0:
                    raise asyncio.TimeoutError
                done, _ = await asyncio.wait({task}, timeout=min(left, 1.0))
                if done:
                    return task.result()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    # ---- queries ---------------------------------------------------------------------------------
    def get(self, job_id: int) -> Job | None:
        return self.jobs.get(int(job_id))

    def select(self, *, active_only: bool = False, kinds: set[str] | None = None) -> list[Job]:
        out = [j for j in self.jobs.values() if (not active_only or j.active) and (kinds is None or j.kind in kinds)]
        return sorted(out, key=lambda j: j.id)

    def running(self) -> list[Job]:
        return self.select(active_only=True)

    def summary(self) -> str:
        """One spoken sentence: what is going on right now."""
        active = self.running()
        if not active:
            done = [j for j in self.jobs.values() if j.status in FINAL]
            if not done:
                return "I'm not working on anything in the background."
            last = max(done, key=lambda j: j.finished or 0)
            return f"Nothing running. Last: {last.label()} to {short(last.goal, 50)} — {last.status}."
        parts = []
        now = self._clock()
        for j in active[:3]:
            what = j.progress[-1] if j.progress and j.status == "running" else j.status.replace("_", " ")
            parts.append(f"{j.label()}, {short(j.goal, 40)}, {int(j.elapsed(now))} seconds in: {short(what, 50)}")
        more = f" And {len(active) - 3} more." if len(active) > 3 else ""
        return "; ".join(parts) + "." + more

    # ---- control ---------------------------------------------------------------------------------
    def submit(self, kind: str, goal: str, work: Callable[[JobContext], Awaitable[str]], *, budget_s: float = 600.0,
               exclusive: str | None = None, private: bool = False,
               announce: bool | Callable[[Job], str | None] = True, max_attempts: int | None = None,
               resumable: bool = False, payload: dict | None = None) -> Job:
        """announce: True = the standard spoken result, False = silent, or a function giving the words."""
        job = Job(id=self._next_id, kind=kind, goal=goal, created=self._clock(), budget_s=float(budget_s),
                  exclusive=exclusive, private=private, resumable=bool(resumable), payload=dict(payload or {}), origin=current_origin(),
                  max_attempts=max(1, int(max_attempts if max_attempts is not None else self._max_attempts)))
        self._next_id += 1
        self.jobs[job.id] = job
        self._persist(job)
        self._tasks[job.id] = asyncio.get_running_loop().create_task(self._run(job, work, announce))
        return job

    # ---- resuming --------------------------------------------------------------------------------
    def register_resumer(self, kind: str, factory: Callable[[Job], Callable]) -> None:
        """factory(job) -> the work function that continues that job (it reads ctx.resumed_from / job.payload)."""
        self._resumers[kind] = factory

    def resumable(self) -> list[Job]:
        if not self._resume_enabled:
            return []
        return [j for j in self.select() if j.status in OFFERED and j.resumable and j.kind in self._resumers]

    def resume(self, job_id: int) -> Job | None:
        """Start an interrupted job again from its checkpoint (same id). None when it cannot be resumed."""
        job = self.get(job_id)
        if job is None or not self._resume_enabled or not job.resumable or job.kind not in self._resumers \
                or job.status not in RESUME_FROM:
            return None
        try:
            work = self._resumers[job.kind](job)
        except Exception:  # noqa: BLE001
            log.warning("could not rebuild job %s for resuming", job.id, exc_info=True)
            return None
        job.status, job.finished, job.result, job.attempts, job.reported = "queued", None, "", 0, True
        job.resumed += 1
        self._persist(job)
        self._tasks[job.id] = asyncio.get_running_loop().create_task(self._run(job, work, True))
        return job

    def cancel_nowait(self, job_id: int) -> bool:
        """Cancel from synchronous code (voice commands). The job's cleanup runs on the event loop."""
        job = self.get(job_id)
        if job is None or not job.active:
            return False
        job.status = "cancelled"  # set first: a queued job then never starts
        task = self._tasks.get(job.id)
        if task is not None and not task.done():
            task.add_done_callback(lambda _t, j=job: self._after_cancel(j))
            task.cancel()
        else:
            self._after_cancel(job)
        return True

    def _after_cancel(self, job: Job) -> None:
        self._tasks.pop(job.id, None)
        if job.finished is None:  # cancelled before it ever started: its own cleanup never ran
            job.finished = self._clock()
            self._persist(job)

    async def cancel(self, job_id: int) -> bool:
        task = self._tasks.get(int(job_id))
        if not self.cancel_nowait(job_id):
            return False
        if task is not None and not task.done():
            await asyncio.wait({task}, timeout=5)
        return True

    def latest_active(self) -> Job | None:
        active = self.running()
        return max(active, key=lambda j: j.created) if active else None

    async def cancel_all(self, kinds: set[str] | None = None) -> list[Job]:
        stopped = []
        for job in self.select(active_only=True, kinds=kinds):
            if await self.cancel(job.id):
                stopped.append(job)
        return stopped

    async def wait(self, job_id: int) -> Job:
        task = self._tasks.get(int(job_id))
        if task is not None:
            await asyncio.wait({task})
        return self.jobs[int(job_id)]

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    # ---- internals -------------------------------------------------------------------------------
    async def _run(self, job: Job, work, announce) -> None:
        if self._slots is None:
            self._slots = asyncio.Semaphore(self._max)
        try:
            # the resource lock first, then a slot: a job waiting for a busy window must not hold a slot that a job
            # needing nothing could use
            lock = self._locks.setdefault(job.exclusive, asyncio.Lock()) if job.exclusive else None
            async with lock if lock is not None else contextlib.nullcontext():
                async with self._slots:
                    if job.status == "cancelled":
                        return
                    job.status, job.started = "running", self._clock()
                    set_origin(job.origin)  # its questions go where the request came from, also after a restart
                    self._persist(job)
                    while True:
                        job.attempts += 1
                        try:
                            result = await self._within_budget(job, work(JobContext(self, job)))
                            break
                        except (asyncio.TimeoutError, asyncio.CancelledError):
                            raise
                        except Exception as e:
                            if job.attempts >= job.max_attempts:
                                if job.max_attempts > 1:
                                    e.add_note(f"after {job.attempts} attempts")
                                raise
                            delay = min(MAX_BACKOFF_S, self._retry_delay * 2 ** (job.attempts - 1))
                            log.warning("job %s attempt %s failed (%s); retrying in %.0f s", job.id, job.attempts,
                                        e, delay)
                            job.progress = (job.progress + [short(
                                f"attempt {job.attempts + 1} of {job.max_attempts} after: {type(e).__name__}: {e}",
                                160)])[-PROGRESS_KEEP:]
                            self._persist(job)
                            await asyncio.sleep(delay)
                    status = "done"
                    if isinstance(result, JobOutcome):
                        status, result = result.status, result.text
                        if status not in OUTCOMES:
                            result, status = f"The work ended with an unknown status {status!r}: {result}", "failed"
                    job.result = " ".join(str(result or "").split()) or "Done."
                    job.status = status
                    if status == "blocked":
                        job.question = job.result
        except asyncio.TimeoutError:
            job.status = "timeout"
            job.result = "Stopped after " + f"{int(job.budget_s)} seconds" + (
                f"; last step: {job.progress[-1]}" if job.progress else "")
        except asyncio.CancelledError:
            if job.status == "cancelled":  # the user asked
                job.result = job.result or "Cancelled."
            else:  # the program is shutting down: tell the user after the restart
                job.status, job.reported = "interrupted", False
                job.result = "Interrupted when Minty restarted."
        except Exception as e:
            log.exception("job %s failed", job.id)
            job.status, job.result = "failed", f"{type(e).__name__}: {e}"
            if job.max_attempts > 1:
                job.result += f" (after {job.attempts} attempts)"
        finally:
            job.finished = self._clock()
            self._persist(job)
            self._tasks.pop(job.id, None)
            if self._tracer is not None:
                self._tracer.event("job", id=job.id, job_kind=job.kind, status=job.status, private=job.private,
                                   seconds=round(job.elapsed(job.finished), 1), progress_steps=len(job.progress))
            for listener in self.on_finish:
                try:
                    listener(job)
                except Exception:  # noqa: BLE001 — bookkeeping must never disturb a job's end
                    log.warning("a job listener failed", exc_info=True)
            if announce and job.status in ("done", "failed", "timeout", "blocked"):
                self._say(announce(job) if callable(announce) else self.announcement(job))

    def announcement(self, job: Job) -> str:
        if job.status == "done":
            return two_sentences(job.result) or f"{job.label().capitalize()} is done."
        if job.status == "blocked":
            return f"{job.label().capitalize()} needs you: {two_sentences(job.result)}"
        if job.status == "timeout":
            return f"The {job.label()} to {short(job.goal, 50)} took too long, so I stopped it."
        return f"The {job.label()} to {short(job.goal, 50)} failed: {two_sentences(job.result, 160)}"

    def _persist(self, job: Job) -> None:
        if self._store is not None:
            self._store.save(job)
        self._publish(job)

    def _publish(self, job: Job) -> None:
        if self._bus is not None:
            self._bus.publish("job", id=job.id, kind=job.kind, goal=short(job.goal, 80), status=job.status,
                              progress=job.progress[-1] if job.progress else "", question=job.question,
                              private=job.private, origin=job.origin)

    def _say(self, text: str) -> None:
        if self._notify is not None and text:
            try:
                self._notify(text)
            except Exception:
                log.warning("job notification failed", exc_info=True)
