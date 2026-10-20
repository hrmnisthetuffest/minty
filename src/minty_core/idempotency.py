"""Idempotency keys: an action that sends, downloads or installs happens once within a window."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

from minty_core.approvals import action_hash


class IdempotencyStore:
    """key -> (result, when). Only successes are kept; a lookup inside the window returns the first result and how
    many seconds ago it was produced."""

    KEEP_S = 7 * 86400  # rows older than this are dropped on open

    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS done (key TEXT PRIMARY KEY, result TEXT NOT NULL, at REAL NOT NULL)")
        self._db.execute("DELETE FROM done WHERE at < ?", (self._clock() - self.KEEP_S,))
        self._db.commit()

    @staticmethod
    def key(name: str, args: dict | None, target: str = "") -> str:
        return action_hash(name, args, target)

    def lookup(self, key: str, window_s: float) -> tuple[str, int] | None:
        now = self._clock()
        with self._lock:
            row = self._db.execute("SELECT result, at FROM done WHERE key=?", (key,)).fetchone()
        if row is None or now - row[1] > window_s:
            return None
        return row[0], int(now - row[1])

    def record(self, key: str, result: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO done(key, result, at) VALUES (?,?,?)",
                             (key, str(result)[:4000], self._clock()))
            self._db.commit()
