"""Spoken progress for long jobs (515): a short "still working" line, rate-limited per job and overall, never for a
private job or a finished one (the job's own notice covers its end)."""
from __future__ import annotations

import time
from typing import Callable


class Narrator:
    def __init__(self, per_job_s: float = 60.0, overall_s: float = 20.0, clock: Callable[[], float] = time.monotonic):
        self.per_job_s, self.overall_s, self._clock = per_job_s, overall_s, clock
        self._job_at: dict[int, float] = {}
        self._last_at: float | None = None

    def say(self, event: dict) -> str | None:
        if event.get("type") != "job" or event.get("status") != "running" or event.get("private"):
            return None
        progress = str(event.get("progress") or "").strip().rstrip(".")
        if not progress:
            return None
        now, job = self._clock(), int(event.get("id") or 0)
        if now - self._job_at.get(job, -1e9) < self.per_job_s:
            return None
        if self._last_at is not None and now - self._last_at < self.overall_s:
            return None
        self._job_at[job], self._last_at = now, now
        goal = str(event.get("goal") or "").strip()
        return f"Still working on {goal}: {progress}." if goal else f"Still working: {progress}."
