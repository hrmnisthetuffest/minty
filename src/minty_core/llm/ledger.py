"""Capacity ledger: how much of each free tier is used today, so routing can spend it wisely.

Counts requests and tokens per (provider, model, UTC day) in SQLite, so the numbers survive restarts. A provider's
own rate-limit headers correct the count when they say less is left. Per-minute limits are tracked in memory.
Limits that are not known never block anything.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

from minty_core.config import ProviderConfig
from minty_core.llm.ratelimit import RateInfo

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (day TEXT, provider TEXT, model TEXT, requests INTEGER, tokens INTEGER,
                                  PRIMARY KEY (day, provider, model));
CREATE TABLE IF NOT EXISTS hint (provider TEXT, model TEXT, remaining_requests INTEGER, until REAL,
                                 PRIMARY KEY (provider, model));
"""


class CapacityLedger:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self._clock = clock
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.executescript(_SCHEMA)
        Path(path).chmod(0o600)
        self._lock = threading.Lock()
        self._minute: dict[tuple[str, str], deque] = {}  # (provider, model) -> (at, tokens) in the last minute

    def _day(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(self._clock()))

    def record(self, provider: str, model: str, tokens_in: int, tokens_out: int, rate: RateInfo | None = None) -> None:
        """One request the provider served (or at least accepted), with its estimated or reported tokens."""
        tokens = max(0, int(tokens_in)) + max(0, int(tokens_out))
        now = self._clock()
        with self._lock:
            self._db.execute(
                "INSERT INTO usage VALUES (?, ?, ?, 1, ?) ON CONFLICT (day, provider, model) DO UPDATE SET "
                "requests = requests + 1, tokens = tokens + excluded.tokens", (self._day(), provider, model, tokens))
            if rate is not None and rate.remaining_requests is not None:
                until = now + (rate.reset_requests_s if rate.reset_requests_s is not None else 3600.0)
                self._db.execute("INSERT OR REPLACE INTO hint VALUES (?, ?, ?, ?)",
                                 (provider, model, rate.remaining_requests, until))
            window = self._minute.setdefault((provider, model), deque())
            window.append((now, tokens))

    def used(self, provider: str, model: str) -> dict[str, int]:
        with self._lock:
            row = self._db.execute("SELECT requests, tokens FROM usage WHERE day = ? AND provider = ? AND model = ?",
                                   (self._day(), provider, model)).fetchone()
        return {"requests": row[0], "tokens": row[1]} if row else {"requests": 0, "tokens": 0}

    def month_tokens(self, provider: str, model: str) -> int:
        """Tokens used this UTC month, for the paid-model spend check."""
        month = time.strftime("%Y-%m-", time.gmtime(self._clock()))
        with self._lock:
            row = self._db.execute("SELECT SUM(tokens) FROM usage WHERE day LIKE ? AND provider = ? AND model = ?",
                                   (month + "%", provider, model)).fetchone()
        return int(row[0] or 0)

    def _hint(self, provider: str, model: str) -> int | None:
        with self._lock:
            row = self._db.execute("SELECT remaining_requests, until FROM hint WHERE provider = ? AND model = ?",
                                   (provider, model)).fetchone()
        return row[0] if row and row[1] > self._clock() else None

    def remaining(self, cfg: ProviderConfig) -> dict:
        """What is left today: requests, tokens, and the smaller share of the two (0..1); None where unknown."""
        limits = cfg.effective_limits()
        used = self.used(cfg.name, cfg.model)
        requests = max(0, limits.rpd - used["requests"]) if limits.rpd else None
        hint = self._hint(cfg.name, cfg.model)
        if hint is not None:
            requests = hint if requests is None else min(requests, hint)
        tokens = max(0, limits.tpd - used["tokens"]) if limits.tpd else None
        shares = [requests / limits.rpd if limits.rpd and requests is not None else None,
                  tokens / limits.tpd if limits.tpd and tokens is not None else None]
        known = [s for s in shares if s is not None]
        return {"requests": requests, "tokens": tokens, "share": round(min(known), 3) if known else None}

    def _last_minute(self, cfg: ProviderConfig) -> tuple[int, int]:
        window = self._minute.get((cfg.name, cfg.model))
        if not window:
            return 0, 0
        cutoff = self._clock() - 60.0
        while window and window[0][0] <= cutoff:
            window.popleft()
        return len(window), sum(t for _at, t in window)

    def can_afford(self, cfg: ProviderConfig, est_tokens: int) -> bool:
        """Would one more request of about est_tokens stay inside every known limit?"""
        limits = cfg.effective_limits()
        left = self.remaining(cfg)
        if left["requests"] is not None and left["requests"] < 1:
            return False
        if left["tokens"] is not None and left["tokens"] < est_tokens:
            return False
        requests, tokens = self._last_minute(cfg)
        if limits.rpm and requests >= limits.rpm:
            return False
        if limits.tpm and tokens + est_tokens > limits.tpm:
            return False
        return True

    def close(self) -> None:
        with self._lock:
            self._db.close()
