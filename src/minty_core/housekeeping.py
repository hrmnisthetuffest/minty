"""Housekeeping: keep the disk from filling and the databases compact, in a quiet window, without touching anything
that is the user's own record.

Trimmable: traces, rotated logs, set-aside conversations, config snapshots, old job-file backups and the browser's
caches. Never touched: history (raw, days, months), memory, the keys file, the journal, workspace files. The window
is skipped (and tried again a little later) while the microphone was used in the last ten minutes.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

MIB = 1024 * 1024
DATABASES = ("memory.db", "approvals.sqlite", "idempotency.sqlite", "receipts.sqlite", "destinations.sqlite",
             "capacity.sqlite", "schedule.db", "library.db", "inbox.db", "health.db", "finance.db", "jobs/jobs.sqlite")
# (folder or glob relative to the config directory, how to order for deletion); the oldest go first
RECLAIMABLE = ("traces/*.jsonl", "minty.log.*", "journal-archive/*.json", "config-snapshots/*.toml",
               "jobs/jobs.jsonl.bak-*", "browser-profile/**/Cache/**/*", "browser-profile/**/Code Cache/**/*",
               "browser-profile/**/GPUCache/**/*")


@dataclass
class DiskReport:
    free_mb: float
    config_mb: float
    biggest: dict[str, float] = field(default_factory=dict)
    low_on_space: bool = False
    over_budget: bool = False


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


class DiskGuard:
    def __init__(self, directory: Path, *, free_floor_mb: float = 1024, max_config_mb: float = 2048,
                 disk_usage: Callable[[str], tuple] = shutil.disk_usage):
        self.directory = Path(directory)
        self.free_floor_mb, self.max_config_mb = free_floor_mb, max_config_mb
        self._usage = disk_usage

    def measure(self) -> DiskReport:
        free = self._usage(str(self.directory))[2] / MIB
        total = _size(self.directory) / MIB if self.directory.exists() else 0.0
        parts = {p.name: _size(p) / MIB for p in self.directory.iterdir()} if self.directory.exists() else {}
        top = dict(sorted(parts.items(), key=lambda kv: -kv[1])[:5])
        return DiskReport(free, total, top, free < self.free_floor_mb, total > self.max_config_mb)

    def candidates(self) -> list[Path]:
        seen: dict[Path, float] = {}
        for pattern in RECLAIMABLE:
            for p in self.directory.glob(pattern):
                if p.is_file():
                    seen[p] = p.stat().st_mtime
        return [p for p, _ in sorted(seen.items(), key=lambda kv: kv[1])]  # oldest first

    def trim(self, force: bool = False) -> list[str]:
        """Delete reclaimable files, oldest first, until the space and size limits are met. Nothing else."""
        report = self.measure()
        if not (report.low_on_space or report.over_budget or force):
            return []
        removed: list[str] = []
        for path in self.candidates():
            report = self.measure()
            if not (report.low_on_space or report.over_budget):
                break
            try:
                path.unlink()
                removed.append(str(path.relative_to(self.directory)))
            except OSError:
                continue
        return removed


def maintain_databases(directory: Path) -> list[str]:
    """WAL checkpoint and VACUUM on each database that exists. Returns what was done."""
    done = []
    for name in DATABASES:
        path = Path(directory) / name
        if not path.exists():
            continue
        try:
            before = path.stat().st_size
            db = sqlite3.connect(path, timeout=5)
            try:
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                db.execute("VACUUM")
            finally:
                db.close()
            done.append(f"{name}: {before // 1024} KB -> {path.stat().st_size // 1024} KB")
        except sqlite3.Error as e:
            done.append(f"{name}: skipped ({type(e).__name__})")
    return done


class MaintenanceWindow:
    """Runs the registered tasks once a day inside the window, when the microphone has been quiet."""

    def __init__(self, state_path: Path, *, at: str = "03:30", length_min: int = 60, quiet_s: float = 600.0,
                 busy_for: Callable[[], float] = lambda: 1e9, clock: Callable[[], datetime] = datetime.now):
        self.state_path, self.length_min, self.quiet_s = Path(state_path), length_min, quiet_s
        hour, _, minute = at.partition(":")
        self._start = (int(hour), int(minute or 0))
        self._busy_for, self._clock = busy_for, clock  # busy_for: seconds since the microphone was last used
        self.tasks: dict[str, Callable[[], str | list[str] | Awaitable[str | list[str]]]] = {}
        self.last: dict[str, str] = {}

    def _last_day(self) -> str:
        try:
            return self.state_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def due(self, now: datetime | None = None) -> bool:
        now = now or self._clock()
        start = now.replace(hour=self._start[0], minute=self._start[1], second=0, microsecond=0)
        minutes = (now - start).total_seconds() / 60
        return 0 <= minutes < self.length_min and self._last_day() != now.strftime("%Y-%m-%d")

    async def run(self, now: datetime | None = None) -> dict[str, str] | None:
        """None when it is not time or the microphone was busy (it tries again on the next call)."""
        now = now or self._clock()
        if not self.due(now) or self._busy_for() < self.quiet_s:
            return None
        results: dict[str, str] = {}
        for name, task in self.tasks.items():
            try:
                out = task()
                if asyncio.iscoroutine(out):
                    out = await out
                results[name] = out if isinstance(out, str) else "; ".join(out) or "nothing to do"
            except Exception as e:  # noqa: BLE001 — one task failing must not skip the rest
                log.warning("maintenance task %s failed", name, exc_info=True)
                results[name] = f"failed: {type(e).__name__}"
        self.last = results
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(now.strftime("%Y-%m-%d"), encoding="utf-8")
        return results
