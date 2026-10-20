"""Outbound destination map: which hosts each plugin has contacted.

The shared HTTP client reports every request's host; the plugin host says which plugin is running (a context
variable survives threads and tasks). Only hostnames are kept, never paths or queries.
"""
from __future__ import annotations

import contextlib
import contextvars
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, Iterator

_current: contextvars.ContextVar[str] = contextvars.ContextVar("minty_plugin", default="core")


@contextlib.contextmanager
def plugin_scope(name: str) -> Iterator[None]:
    token = _current.set(name)
    try:
        yield
    finally:
        _current.reset(token)


def current_plugin() -> str:
    return _current.get()


def _size(n: int) -> str:
    if n < 1024:
        return f"{n} bytes"
    return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


class DestinationMap:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS dest (plugin TEXT, host TEXT, n INTEGER, first REAL, last REAL,"
                         " PRIMARY KEY (plugin, host))")
        self._db.execute("CREATE TABLE IF NOT EXISTS daily (day TEXT, plugin TEXT, host TEXT, n INTEGER, sent INTEGER,"
                         " PRIMARY KEY (day, plugin, host))")
        self._db.execute("DELETE FROM daily WHERE day < date(?, 'unixepoch', '-30 days')", (self._clock(),))
        self._db.commit()

    def record(self, plugin: str, host: str, sent_bytes: int = 0) -> None:
        host = (host or "").lower().strip(".")
        if not host:
            return
        now = self._clock()
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        with self._lock:
            self._db.execute(
                "INSERT INTO dest(plugin, host, n, first, last) VALUES (?,?,1,?,?) "
                "ON CONFLICT(plugin, host) DO UPDATE SET n = n + 1, last = excluded.last", (plugin, host, now, now))
            self._db.execute(
                "INSERT INTO daily(day, plugin, host, n, sent) VALUES (?,?,?,1,?) "
                "ON CONFLICT(day, plugin, host) DO UPDATE SET n = n + 1, sent = sent + excluded.sent",
                (day, plugin, host, int(sent_bytes)))
            self._db.commit()

    def today(self) -> list[tuple[str, str, int, int]]:
        """(plugin, host, requests, bytes sent) for today, biggest first. Bytes are the request bodies."""
        day = time.strftime("%Y-%m-%d", time.localtime(self._clock()))
        with self._lock:
            return self._db.execute("SELECT plugin, host, n, sent FROM daily WHERE day = ? ORDER BY sent DESC, n DESC",
                                    (day,)).fetchall()

    def today_summary(self) -> str:
        rows = self.today()
        if not rows:
            return "Nothing has left this computer today."
        total = sum(r[3] for r in rows)
        top = "; ".join(f"{host} {n} request{'s' * (n != 1)} from {plugin}, {_size(sent)}"
                        for plugin, host, n, sent in rows[:4])
        more = f" And {len(rows) - 4} more places." if len(rows) > 4 else ""
        return f"Today I sent {_size(total)} to {len(rows)} places. {top}.{more}"

    def listing(self, plugin: str | None = None) -> dict[str, list[tuple[str, int]]]:
        sql, args = "SELECT plugin, host, n FROM dest", ()
        if plugin:
            sql, args = sql + " WHERE plugin = ?", (plugin,)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY plugin, n DESC", args).fetchall()
        out: dict[str, list[tuple[str, int]]] = {}
        for p, host, n in rows:
            out.setdefault(p, []).append((host, n))
        return out

    def summary(self, plugin: str | None = None) -> str:
        found = self.listing(plugin)
        if not found:
            return ("Nothing has connected out yet." if not plugin else f"{plugin} has not connected to anything.")
        lines = []
        for name, hosts in found.items():
            shown = ", ".join(h for h, _ in hosts[:4]) + (f" and {len(hosts) - 4} more" if len(hosts) > 4 else "")
            lines.append(f"{name}: {shown}")
        return "; ".join(lines[:6]) + "."


_registry: DestinationMap | None = None


def set_registry(destinations: DestinationMap | None) -> None:
    global _registry
    _registry = destinations


async def observe(request) -> None:  # an httpx "request" event hook
    if _registry is not None:
        try:
            sent = request.headers.get("content-length", "0")
            _registry.record(current_plugin(), request.url.host, int(sent) if sent.isdigit() else 0)
        except Exception:  # noqa: BLE001 — bookkeeping never breaks a request
            pass


def hooks() -> dict:
    return {"request": [observe]}
