"""Action receipts: every change Minty made (or was stopped from making) is written down with why.

A receipt holds what was done, the request that led to it, the approval behind it if one was asked, and how it
ended. "Why did you do that?" is answered from the newest one, never from the model's memory.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

KEEP_S = 90 * 86400
OUTCOMES = ("ok", "failed", "declined", "blocked")


@dataclass(frozen=True)
class Receipt:
    id: int
    ts: float
    turn_id: str
    tool: str
    risk: int
    summary: str
    request: str
    approval_id: int | None
    outcome: str
    verified: str
    detail: str
    machine: str = ""


_COLS = "id, ts, turn_id, tool, risk, summary, request, approval_id, outcome, verified, detail, machine"


class ReceiptStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time, redact: Callable[[str], str] | None = None,
                 machine: str = ""):
        self.machine = machine  # which computer made the change (one bot and one store per machine)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock, self._redact = clock, redact or (lambda text: text)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS receipts (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,"
            " turn_id TEXT DEFAULT '', tool TEXT NOT NULL, risk INTEGER NOT NULL, summary TEXT NOT NULL,"
            " request TEXT DEFAULT '', approval_id INTEGER, outcome TEXT NOT NULL, verified TEXT DEFAULT 'unverified',"
            " detail TEXT DEFAULT '')")
        if "machine" not in [r[1] for r in self._db.execute("PRAGMA table_info(receipts)")]:
            self._db.execute("ALTER TABLE receipts ADD COLUMN machine TEXT DEFAULT ''")
        self._db.execute("DELETE FROM receipts WHERE ts < ?", (self._clock() - KEEP_S,))
        self._db.commit()

    def record(self, tool: str, risk: int, summary: str, *, request: str = "", approval_id: int | None = None,
               outcome: str = "ok", verified: str = "unverified", detail: str = "", turn_id: str = "") -> int:
        clean = self._redact
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO receipts(ts, turn_id, tool, risk, summary, request, approval_id, outcome, verified, detail,"
                " machine) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (self._clock(), turn_id, tool, int(risk), clean(summary)[:200], clean(request)[:240], approval_id,
                 outcome, verified, clean(detail)[:240], self.machine))
            self._db.commit()
            return cur.lastrowid

    def recent(self, limit: int = 10, *, outcomes: tuple[str, ...] | None = None) -> list[Receipt]:
        sql = f"SELECT {_COLS} FROM receipts"
        args: list = []
        if outcomes:
            sql += " WHERE outcome IN (%s)" % ",".join("?" for _ in outcomes)
            args += list(outcomes)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, int(limit))).fetchall()
        return [Receipt(*r) for r in rows]

    def since(self, ts: float) -> list[Receipt]:
        with self._lock:
            rows = self._db.execute(f"SELECT {_COLS} FROM receipts WHERE ts >= ? ORDER BY id", (ts,)).fetchall()
        return [Receipt(*r) for r in rows]


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def why_reply(store: ReceiptStore, approvals=None) -> str:
    """The honest answer to "why did you do that?", from the newest receipt."""
    recent = store.recent(1)
    if not recent:
        return "I haven't changed anything yet, so there is nothing to explain."
    r = recent[0]
    ask = f" You asked: {r.request}." if r.request else ""
    if r.outcome == "declined":
        return f"I did not {r.summary}: you said no.{ask}".strip()
    if r.outcome == "blocked":
        return f"I did not {r.summary}: a safety check stopped it ({r.detail}).{ask}".strip()
    approved = ""
    if r.approval_id is not None:
        approved = f" You said yes to it at {_when(r.ts)}."
    elif r.risk >= 1:
        approved = " It was a small change that needed no question."
    result = "It worked." if r.outcome == "ok" else f"It failed: {r.detail}." if r.detail else "It failed."
    return f"At {_when(r.ts)} I did this: {r.summary}.{ask}{approved} {result}".strip()


def today_reply(store: ReceiptStore) -> str:
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    done = [r for r in store.since(midnight) if r.outcome in ("ok", "failed")]
    if not done:
        return "I haven't changed anything today."
    lines = "; ".join(f"{_when(r.ts)} {r.summary}" for r in done[-5:])
    return f"{len(done)} change{'s' * (len(done) != 1)} today. The latest: {lines}."
