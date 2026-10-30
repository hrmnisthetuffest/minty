"""A watchdog that is not the agent: it watches the work from outside and has the authority to stop it.

Deterministic, run from the app's own loop every half minute, and with no tool the model can call to switch it off or change its limits (they are constants here),
so ordinary reasoning, a page or an email cannot argue with it. What it does:

* **Stalled work.** A background job shows life when it reports progress, changes state or gets an answer to a question. No sign of life for a while marks it `stalled` (and
  says so once); much longer and it is cancelled, with the reason on the job. Time spent waiting for the owner's answer is never a stall (those states are skipped, and an
  answer counts as life). A job that is long but keeps moving is left alone.
* **Looping work.** The same progress line over and over is `looping`; far more of it and the job is cancelled.
* **Slow work.** Much longer than similar finished jobs took is marked `slow` (no action: slow is not wrong).
* **State that does not match.** A job recorded as running with nothing running it is marked failed, not left "running" for ever.
* **A workflow that keeps failing.** After three real runs in a row that failed or were blocked, the workflow is switched off (it can be switched on again) and the owner is told once.
Every action is said through the notifier (de-duplicated, with quiet hours), and `findings()` lists what is wrong now for the diagnosis and for Mission Control.
"""
from __future__ import annotations

import time
from typing import Callable

STALL_S = {"mission": 900.0, "flow": 1200.0}
DEFAULT_STALL_S = 900.0
CANCEL_AFTER = 3  # cancelled after this many stall periods with no sign of life
LOOP_SAME, LOOP_CANCEL = 5, 12  # the same progress line this many times in a row: looping; this many: cancelled
SLOW_FACTOR, SLOW_MIN_S, SLOW_NEEDS = 3.0, 120.0, 3
BREAKER = 3
WAITING = ("waiting_approval", "needs_user", "queued")


def _norm(line: str) -> str:
    return " ".join(str(line).lower().split())  # numbers stay: "found 3 items", "found 4 items" is progress, not a loop


class Watchdog:
    def __init__(self, jobs, flows=None, notifier: Callable | None = None, clock: Callable[[], float] = time.time):
        self._jobs, self._flows, self._notifier, self._clock = jobs, flows, notifier, clock
        self._found: dict[str, dict] = {}

    def _tell(self, key: str, severity: str, title: str, body: str, action: str = "", taken: str = "") -> None:
        n = self._notifier() if self._notifier else None
        if n is None:
            return
        from minty_core.notifier import Event

        n.publish(Event("watchdog", key, severity, title, body, action, fingerprint=key, taken=taken))

    def _save(self, job) -> None:
        """Keep the job's health on record without that counting as a sign of life."""
        touched = job.touched
        self._jobs._persist(job)
        job.touched = touched

    def _stall_s(self, job) -> float:
        return STALL_S.get(job.kind, DEFAULT_STALL_S)

    def _median_s(self, kind: str) -> float | None:
        done = sorted((j.finished - j.started) for j in self._jobs.jobs.values() if j.kind == kind and j.status == "done" and j.started and j.finished)[-20:]
        return done[len(done) // 2] if len(done) >= SLOW_NEEDS else None

    async def tick(self) -> list[dict]:
        """One look at everything running. Returns what it did (for the tests and the log)."""
        now, did = self._clock(), []
        self._found = {k: v for k, v in self._found.items() if v.get("sticky")}
        for job in self._jobs.select(active_only=True):
            if job.status in WAITING:
                job.health = "ok" if job.health in ("stalled", "slow") and job.status == "waiting_approval" else job.health
                continue
            task = self._jobs._tasks.get(job.id)
            idle = now - (job.touched or job.started or job.created)
            if job.status == "running" and (task is None or task.done()) and idle > 30:
                job.status, job.result, job.finished = "failed", "Stopped by the watchdog: it was recorded as running but nothing was running it.", now
                self._save(job)
                self._found[f"job:{job.id}:mismatch"] = {"kind": "state mismatch", "verified": True, "about": f"job:{job.id}", "summary": f"{job.label()} was recorded as running with nothing running it: marked failed"}
                self._tell(f"job:{job.id}:mismatch", "notable", f"{job.label().capitalize()} had silently stopped", "It was marked running but nothing was running it, so I marked it failed.", "Start it again if you still want it.", "marked failed")
                did.append({"job": job.id, "did": "marked failed"})
                continue
            lines = [_norm(x) for x in job.progress[-LOOP_CANCEL:]]
            same = 0
            for x in reversed(lines):
                if lines and x == lines[-1]:
                    same += 1
                else:
                    break
            stall = self._stall_s(job)
            if same >= LOOP_CANCEL or idle >= stall * CANCEL_AFTER:
                why = (f"the same step ({job.progress[-1][:60]!r}) repeated {same} times" if same >= LOOP_CANCEL else f"no progress for {int(idle // 60)} minutes")
                await self._jobs.cancel(job.id)
                job.result, job.health = f"Stopped by the watchdog: {why}.", "stalled"
                self._save(job)
                self._found[f"job:{job.id}:cancelled"] = {"kind": "cancelled by the watchdog", "verified": True, "about": f"job:{job.id}", "summary": f"{job.label()} stopped: {why}", "sticky": False}
                self._tell(f"job:{job.id}:cancelled", "important", f"I stopped {job.label()}", f"{why[0].upper() + why[1:]}, so it was not producing anything.", "Tell me if it should be tried differently.", "cancelled")
                did.append({"job": job.id, "did": "cancelled", "why": why})
                continue
            if same >= LOOP_SAME:
                if job.health != "looping":
                    job.health = "looping"
                    self._save(job)
                    self._tell(f"job:{job.id}:looping", "notable", f"{job.label().capitalize()} is repeating itself", f"The same step ({job.progress[-1][:60]!r}) has come up {same} times in a row.", "I will stop it if it goes on.", "")
                    did.append({"job": job.id, "did": "looping"})
                self._found[f"job:{job.id}:looping"] = {"kind": "looping", "verified": True, "about": f"job:{job.id}", "summary": f"{job.label()} repeated {job.progress[-1][:50]!r} {same} times"}
            elif idle >= stall:
                if job.health != "stalled":
                    job.health = "stalled"
                    self._save(job)
                    self._tell(f"job:{job.id}:stalled", "notable", f"{job.label().capitalize()} has stopped moving", f"Nothing has happened for {int(idle // 60)} minutes (last: {(job.progress[-1] if job.progress else 'nothing yet')[:60]}).", "I will stop it if it stays stuck.", "")
                    did.append({"job": job.id, "did": "stalled"})
                self._found[f"job:{job.id}:stalled"] = {"kind": "stalled", "verified": True, "about": f"job:{job.id}", "summary": f"{job.label()} has not moved for {int(idle // 60)} minutes"}
            else:
                med = self._median_s(job.kind)
                slow = med is not None and (now - (job.started or now)) > max(SLOW_MIN_S, med * SLOW_FACTOR)
                if slow:
                    job.health = "slow"
                    self._found[f"job:{job.id}:slow"] = {"kind": "slow", "verified": True, "about": f"job:{job.id}", "summary": f"{job.label()} has run {int((now - job.started) // 60)} min; similar jobs took about {int(med // 60) or 1} min (still moving, so it is left alone)"}
                elif job.health != "ok":
                    job.health = "ok"
                    self._save(job)
        did += self._breaker()
        return did

    def _breaker(self) -> list[dict]:
        """A workflow whose last real runs all failed is switched off: running it again and again helps nobody."""
        did = []
        if self._flows is None:
            return did
        store = self._flows.store
        for f in store.list():
            if not f.enabled:
                continue
            runs = [r for r in store.runs(f.id, 8) if r["mode"] == "real" and r["status"] != "running"][:BREAKER]
            if len(runs) == BREAKER and all(r["status"] in ("failed", "blocked") for r in runs):
                store.set_enabled(f.id, False)
                why = runs[0]["error"][:100] or runs[0]["status"]
                self._found[f"flow:{f.id}:breaker"] = {"kind": "workflow switched off", "verified": True, "about": f"flow:{f.name}", "summary": f"{f.name} failed {BREAKER} real runs in a row ({why}) and was switched off", "sticky": True}
                self._tell(f"flow:{f.id}:breaker:{runs[0]['id']}", "important", f"I switched off the workflow {f.name}", f"Its last {BREAKER} runs all failed (latest: {why}).", "Look at the runs, fix it, and switch it on again.", "switched off")
                did.append({"flow": f.id, "did": "switched off"})
        return did

    def findings(self) -> list[dict]:
        return list(self._found.values())
