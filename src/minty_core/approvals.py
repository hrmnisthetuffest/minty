"""Approvals: a yes is bound to the exact action it approved.

Every question the safety gate asks becomes a record here. A record carries a hash of the action (tool, normalised
arguments, target), an expiry and a status. Consuming it checks that the action being run is the one that was
approved, that it has not expired and that it is used once; anything else refuses (fail closed). Arguments are not
stored, only their hash, so the table holds no message text or credentials.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from minty_core.origin import LAPTOP, current_origin

DEFAULT_TTL_S = 300.0
KEEP_S = 30 * 86400  # decided and expired records older than this are dropped when the store opens
PENDING, APPROVED, DENIED, EXPIRED, USED = "pending", "approved", "denied", "expired", "used"


def _norm(value):
    if isinstance(value, dict):
        return {str(k): _norm(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_norm(v) for v in value]
    if isinstance(value, str):
        return " ".join(unicodedata.normalize("NFC", value).split())
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def action_hash(name: str, args: dict | None, target: str = "") -> str:
    """The same action always hashes the same (argument order, spacing and 1 vs 1.0 do not matter); a different
    tool, argument or target never does."""
    canon = {"tool": str(name), "args": _norm(args or {}), "target": _norm(str(target or ""))}
    text = json.dumps(canon, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Approval:
    id: int
    hash: str
    tool: str
    question: str
    risk: int
    status: str
    created: float
    expires: float
    decided: float | None = None
    via: str = ""
    job_id: int | None = None


_COLS = "id, hash, tool, question, risk, status, created, expires, decided, via, job_id"


class ApprovalStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time, ttl_s: float = DEFAULT_TTL_S):
        self.path = Path(path)
        self._origins: dict[int, str] = {}  # question -> where the request came from (kept while the app runs: questions are short-lived)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock, self._ttl = clock, float(ttl_s)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS approvals (id INTEGER PRIMARY KEY AUTOINCREMENT, hash TEXT NOT NULL,"
            " tool TEXT NOT NULL, question TEXT NOT NULL, risk INTEGER NOT NULL, status TEXT NOT NULL,"
            " created REAL NOT NULL, expires REAL NOT NULL, decided REAL, via TEXT DEFAULT '', job_id INTEGER)")
        self._db.execute("DELETE FROM approvals WHERE status != ? AND created < ?", (PENDING, self._clock() - KEEP_S))
        self._db.commit()

    # ---- writing -------------------------------------------------------------------------------
    def request(self, name: str, args: dict | None, question: str, risk: int = 0, *, target: str = "",
                ttl_s: float | None = None, job_id: int | None = None, origin: str | None = None) -> Approval:
        now = self._clock()
        ttl = self._ttl if ttl_s is None else float(ttl_s)
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO approvals(hash, tool, question, risk, status, created, expires, job_id)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (action_hash(name, args, target), name, str(question)[:300], int(risk), PENDING, now, now + ttl,
                 job_id))
            self._db.commit()
            self._origins[cur.lastrowid] = origin or current_origin()
            while len(self._origins) > 500:
                self._origins.pop(next(iter(self._origins)))
            return self._get(cur.lastrowid)

    def origin_of(self, ident: int) -> str:
        """"laptop" or "telegram": where the request behind this question came from (a question from before a restart counts as the laptop's)."""
        return self._origins.get(int(ident), LAPTOP)

    def decide(self, ident: int, approved: bool, via: str = "voice") -> bool:
        """Only a pending, unexpired approval can be decided, and only once."""
        now = self._clock()
        with self._lock:
            self._expire(now)
            cur = self._db.execute(
                "UPDATE approvals SET status=?, decided=?, via=? WHERE id=? AND status=?",
                (APPROVED if approved else DENIED, now, via, int(ident), PENDING))
            self._db.commit()
            return cur.rowcount == 1

    def consume(self, ident: int, name: str, args: dict | None, target: str = "") -> bool:
        """True exactly once, for the approved action itself, before it expires."""
        now = self._clock()
        with self._lock:
            self._expire(now)
            cur = self._db.execute(
                "UPDATE approvals SET status=? WHERE id=? AND status=? AND hash=? AND expires>?",
                (USED, int(ident), APPROVED, action_hash(name, args, target), now))
            self._db.commit()
            return cur.rowcount == 1

    def history(self, since: float) -> list[Approval]:
        """Every question asked since a time that has been decided or has expired (one still waiting is not counted yet), oldest first."""
        with self._lock:
            self._expire(self._clock())
            rows = self._db.execute("SELECT id, hash, tool, question, risk, status, created, expires, decided, via, job_id FROM approvals "
                                    "WHERE created >= ? AND status != ? ORDER BY created, id", (float(since), PENDING)).fetchall()
        return [Approval(*r) for r in rows]

    def find_pending(self, question: str) -> "Approval | None":
        """The newest open question with exactly these words that no job is waiting on yet (the one the safety gate just opened)."""
        with self._lock:
            self._expire(self._clock())
            row = self._db.execute(
                f"SELECT {_COLS} FROM approvals WHERE status=? AND question=? AND job_id IS NULL ORDER BY id DESC LIMIT 1",
                (PENDING, str(question)[:300])).fetchone()
        return Approval(*row) if row else None

    def extend(self, ident: int, seconds: float) -> None:
        """Set how long an open question has from now to be answered (a background job can wait hours; a spoken question cannot)."""
        with self._lock:
            self._db.execute("UPDATE approvals SET expires=? WHERE id=? AND status IN (?,?)",
                             (self._clock() + float(seconds), int(ident), PENDING, APPROVED))
            self._db.commit()

    def attach_job(self, ident: int, job_id: int) -> None:
        with self._lock:
            self._db.execute("UPDATE approvals SET job_id=? WHERE id=? AND job_id IS NULL", (int(job_id), int(ident)))
            self._db.commit()

    def deny_all(self, via: str = "lockdown") -> int:
        """Refuse everything still open: waiting questions and approvals not yet used."""
        with self._lock:
            cur = self._db.execute("UPDATE approvals SET status=?, decided=?, via=? WHERE status IN (?,?)",
                                   (DENIED, self._clock(), via, PENDING, APPROVED))
            self._db.commit()
            return cur.rowcount

    def expire_due(self) -> int:
        with self._lock:
            return self._expire(self._clock())

    def _expire(self, now: float) -> int:
        cur = self._db.execute("UPDATE approvals SET status=? WHERE status IN (?,?) AND expires<=?",
                               (EXPIRED, PENDING, APPROVED, now))
        self._db.commit()
        return cur.rowcount

    # ---- reading -------------------------------------------------------------------------------
    def _get(self, ident: int) -> Approval | None:
        row = self._db.execute(f"SELECT {_COLS} FROM approvals WHERE id=?", (int(ident),)).fetchone()
        return Approval(*row) if row else None

    def get(self, ident: int) -> Approval | None:
        with self._lock:
            self._expire(self._clock())
            return self._get(ident)

    def pending(self, limit: int = 20) -> list[Approval]:
        with self._lock:
            self._expire(self._clock())
            rows = self._db.execute(
                f"SELECT {_COLS} FROM approvals WHERE status=? ORDER BY id DESC LIMIT ?", (PENDING, int(limit))
            ).fetchall()
        return [Approval(*r) for r in reversed(rows)]
