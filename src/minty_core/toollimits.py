"""Hourly limits per tool and an autonomy budget, so a runaway loop stops long before a quota does.

Only calls that change something (risk class 1 and up) are counted; reading is never limited here. The window is a
rolling hour. A limit of 0 means no limit.
"""
from __future__ import annotations

import fnmatch
import time
from collections import defaultdict, deque
from typing import Callable

WINDOW_S = 3600.0


class ToolLimits:
    def __init__(self, store, clock: Callable[[], float] = time.time):
        self._store, self._clock = store, clock
        self._calls: dict[str, deque] = defaultdict(deque)  # tool -> timestamps
        self._all: deque = deque()  # every counted call (the autonomy budget)

    # ---- settings ------------------------------------------------------------------------------
    def _limit_for(self, name: str, risk: int) -> int:
        cfg = self._store.config.safety
        for pattern, limit in cfg.tool_hourly_limits.items():
            if fnmatch.fnmatchcase(name, pattern):
                return int(limit)
        return int(cfg.default_hourly_limit_changes) if risk >= 2 else 0

    # ---- the question and the bookkeeping --------------------------------------------------------
    def _trim(self, now: float) -> None:
        for q in (self._all, *self._calls.values()):
            while q and now - q[0] >= WINDOW_S:
                q.popleft()

    def check(self, name: str, risk: int) -> str | None:
        """A spoken reason when this call is over a limit, else None. Does not count the call."""
        if risk < 1:
            return None
        now = self._clock()
        self._trim(now)
        limit = self._limit_for(name, risk)
        if limit and len(self._calls[name]) >= limit:
            return (f"{name.replace('_', ' ')} has already run {len(self._calls[name])} times in the last hour, "
                    "which looks like a loop, so I stopped it")
        budget = int(self._store.config.safety.autonomy_budget_per_hour)
        if budget and len(self._all) >= budget:
            return (f"I have already made {len(self._all)} changes on my own in the last hour, "
                    "which is my limit; I will ask you before doing more")
        return None

    def record(self, name: str, risk: int) -> None:
        if risk < 1:
            return
        now = self._clock()
        self._calls[name].append(now)
        self._all.append(now)

    def usage(self) -> dict[str, int]:
        self._trim(self._clock())
        return {n: len(q) for n, q in self._calls.items() if q}

    def reset(self) -> None:
        self._calls.clear()
        self._all.clear()
