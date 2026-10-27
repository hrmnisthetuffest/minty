"""The outbox: every message Minty drafted or sent for the user, and exactly what happened to it.

A send is claimed before it starts (drafted/failed -> sending, once, atomically), so two calls at the same moment send one
message. A send that was cut off after the message left (a crash, a dropped connection) is `unknown`: it may have gone, so
it is never re-sent by itself, only reported. The table is the record of what Minty did in the user's name.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

DRAFTED, SENDING, SENT, FAILED, UNKNOWN, DISCARDED = "drafted", "sending", "sent", "failed", "unknown", "discarded"
DAY_S = 86400


@dataclass(frozen=True)
class Item:
    id: int
    created: float
    to: str  # comma separated
    subject: str
    body: str
    reply: bool
    authenticated: int | None  # 1, 0 or None (not known)
    original_subject: str
    in_reply_to: str
    references: str
    follow_up_days: int
    status: str
    how: str  # auto | approved | ''
    message_id: str
    error: str
    updated: float

    @property
    def recipients(self) -> tuple[str, ...]:
        return tuple(a for a in self.to.split(",") if a)


_COLS = ("id, created, to_addr, subject, body, reply, authenticated, original_subject, in_reply_to, refs, follow_up_days,"
         " status, how, message_id, error, updated")


class OutboxStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock, self._lock = clock, threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL NOT NULL,"
            " to_addr TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL, reply INTEGER NOT NULL,"
            " authenticated INTEGER, original_subject TEXT NOT NULL DEFAULT '', in_reply_to TEXT NOT NULL DEFAULT '',"
            " refs TEXT NOT NULL DEFAULT '', follow_up_days INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,"
            " how TEXT NOT NULL DEFAULT '', message_id TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',"
            " updated REAL NOT NULL)")
        # a send that was under way when the program stopped may or may not have gone
        self._db.execute("UPDATE outbox SET status=?, error=?, updated=? WHERE status=?",
                         (UNKNOWN, "the program stopped while this was being sent", self._clock(), SENDING))
        self._db.commit()

    def add(self, *, to: tuple[str, ...], subject: str, body: str, reply: bool, authenticated: bool | None,
            original_subject: str = "", in_reply_to: str = "", references: str = "", follow_up_days: int = 0) -> Item:
        now = self._clock()
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO outbox(created, to_addr, subject, body, reply, authenticated, original_subject, in_reply_to,"
                " refs, follow_up_days, status, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (now, ",".join(to), subject, body, int(reply), None if authenticated is None else int(authenticated),
                 original_subject, in_reply_to, references, follow_up_days, DRAFTED, now))
            self._db.commit()
        return self.get(cur.lastrowid)

    def get(self, ident: int) -> Item | None:
        row = self._db.execute(f"SELECT {_COLS} FROM outbox WHERE id=?", (int(ident),)).fetchone()
        return self._item(row) if row else None

    @staticmethod
    def _item(row) -> Item:
        r = list(row)
        r[5] = bool(r[5])
        return Item(*r)

    def claim(self, ident: int, how: str) -> bool:
        """True for exactly one caller: the one that moved a drafted (or failed) item to sending."""
        with self._lock:
            cur = self._db.execute("UPDATE outbox SET status=?, how=?, updated=? WHERE id=? AND status IN (?,?)",
                                   (SENDING, how, self._clock(), int(ident), DRAFTED, FAILED))
            self._db.commit()
            return cur.rowcount == 1

    def finish(self, ident: int, status: str, *, message_id: str = "", error: str = "") -> None:
        with self._lock:
            self._db.execute("UPDATE outbox SET status=?, message_id=?, error=?, updated=? WHERE id=?",
                             (status, message_id, error[:300], self._clock(), int(ident)))
            self._db.commit()

    def discard(self, ident: int) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE outbox SET status=?, updated=? WHERE id=? AND status IN (?,?,?)",
                                   (DISCARDED, self._clock(), int(ident), DRAFTED, FAILED, UNKNOWN))
            self._db.commit()
            return cur.rowcount == 1

    def sent_today(self, how: str | None = None) -> int:
        """Messages that went out in the last 24 hours (optionally only those sent without asking)."""
        since = self._clock() - DAY_S
        sql, args = "SELECT COUNT(*) FROM outbox WHERE status=? AND updated>=?", [SENT, since]
        if how:
            sql, args = sql + " AND how=?", args + [how]
        return self._db.execute(sql, args).fetchone()[0]

    def recent(self, limit: int = 10) -> list[Item]:
        rows = self._db.execute(f"SELECT {_COLS} FROM outbox WHERE status!=? ORDER BY id DESC LIMIT ?",
                                (DISCARDED, int(limit))).fetchall()
        return [self._item(r) for r in rows]

    def track_record(self) -> dict[str, dict[str, int]]:
        """For each single recipient: how many replies they said yes to and how many sends failed or are unknown."""
        rows = self._db.execute(
            "SELECT to_addr, status, how, reply, COUNT(*) FROM outbox WHERE to_addr NOT LIKE '%,%' GROUP BY to_addr, status, how, reply"
        ).fetchall()
        record: dict[str, dict[str, int]] = {}
        for addr, status, how, reply, n in rows:
            r = record.setdefault(addr.lower(), {"approved_replies": 0, "bad": 0})
            if status == SENT and how == "approved" and reply:
                r["approved_replies"] += n
            elif status in (FAILED, UNKNOWN):
                r["bad"] += n
        return record
