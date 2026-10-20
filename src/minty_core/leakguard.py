"""Leak guard: sample memory, file descriptors, threads, SQLite WAL size and queue length, and alarm on a trend.

A 24/7 process fails slowly, not suddenly. The guard keeps a bounded series of samples and fits a straight line:
memory growth over the window (after a warm-up) above a share, descriptors or threads climbing, a write-ahead log
that never shrinks, a queue that only grows. Nothing here changes behaviour; it names the problem early.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Sample:
    ts: float
    rss_mb: float
    fds: int
    threads: int
    wal_mb: float
    queue: int = 0


def read_rss_mb() -> float:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def take(directory: Path | None = None, queue: int = 0, clock=time.time) -> Sample:
    try:
        fds = len(os.listdir("/proc/self/fd"))
    except OSError:
        fds = 0
    wal = 0.0
    if directory is not None:
        wal = sum(p.stat().st_size for p in Path(directory).rglob("*-wal") if p.is_file()) / 2**20
    return Sample(clock(), read_rss_mb(), fds, threading.active_count(), wal, queue)


def fitted_change(values: list[float]) -> tuple[float, float]:
    """(change over the series, change as a share of the start) from a least-squares line, so one spike is not a
    trend."""
    n = len(values)
    if n < 3:
        return 0.0, 0.0
    mean_x, mean_y = (n - 1) / 2, sum(values) / n
    num = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    den = sum((i - mean_x) ** 2 for i in range(n))
    slope = num / den
    start = mean_y - slope * mean_x
    change = slope * (n - 1)
    return change, (change / start if start > 0 else 0.0)


class LeakGuard:
    def __init__(self, window: int = 288, warmup: float = 0.25, rss_growth: float = 0.10, fd_growth: int = 8,
                 thread_growth: int = 4, wal_mb: float = 64.0, queue_max: int = 100):
        self.samples: deque[Sample] = deque(maxlen=window)
        self.warmup, self.rss_growth = warmup, rss_growth
        self.fd_growth, self.thread_growth, self.wal_mb, self.queue_max = fd_growth, thread_growth, wal_mb, queue_max

    def add(self, sample: Sample) -> None:
        self.samples.append(sample)

    def alarms(self) -> list[str]:
        s = list(self.samples)
        if len(s) < 12:  # too little to call anything a trend
            return []
        s = s[int(len(s) * self.warmup):]  # start-up growth is not a leak
        out = []
        change, share = fitted_change([x.rss_mb for x in s])
        if share > self.rss_growth and change > 20:
            out.append(f"memory is growing: about {change:.0f} MB more over the window ({share * 100:.0f} %)")
        grow, _ = fitted_change([float(x.fds) for x in s])
        if grow > self.fd_growth:
            out.append(f"open files are climbing: about {grow:.0f} more")
        grow, _ = fitted_change([float(x.threads) for x in s])
        if grow > self.thread_growth:
            out.append(f"threads are climbing: about {grow:.0f} more")
        if s[-1].wal_mb > self.wal_mb:
            out.append(f"a database log has reached {s[-1].wal_mb:.0f} MB and is not shrinking")
        if s[-1].queue > self.queue_max:
            out.append(f"{s[-1].queue} items are waiting in a queue")
        return out
