"""The butler's one store: items of every domain, an undo journal, export and real deletion. Owner-only file."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

DAY = 86400


@dataclass
class Item:
    id: int
    domain: str
    name: str
    data: dict = field(default_factory=dict)
    due: float | None = None
    every_days: int | None = None
    status: str = "open"  # open | done
    created: float = 0.0
    updated: float = 0.0
    notified: float | None = None
    source: str = "user"


_COLS = "id, domain, name, data, due, every_days, status, created, updated, notified, source"


def _item(r) -> Item:
    return Item(r[0], r[1], r[2], json.loads(r[3] or "{}"), r[4], r[5], r[6], r[7], r[8], r[9], r[10])


class ButlerStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.RLock()
        fresh = not self.path.exists()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT NOT NULL,"
            " name TEXT NOT NULL, data TEXT DEFAULT '{}', due REAL, every_days INTEGER, status TEXT DEFAULT 'open',"
            " created REAL, updated REAL, notified REAL, source TEXT DEFAULT 'user');"
            "CREATE INDEX IF NOT EXISTS items_domain ON items(domain, status);"
            "CREATE TABLE IF NOT EXISTS undo (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, label TEXT,"
            " action TEXT, ref TEXT, done INTEGER DEFAULT 0);"
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);")
        self._db.commit()
        if fresh:
            os.chmod(self.path, 0o600)

    # ---- writing -------------------------------------------------------------------------------------
    def add(self, domain: str, name: str, *, data: dict | None = None, due: float | None = None,
            every_days: int | None = None, source: str = "user") -> Item:
        now = self._clock()
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO items(domain, name, data, due, every_days, status, created, updated, source)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (domain, name, json.dumps(data or {}), due, every_days, "open", now, now, source))
            self._db.commit()
            return self.get(cur.lastrowid)

    def update(self, ident: int, **fields) -> Item | None:
        allowed = {"name", "data", "due", "every_days", "status", "notified"}
        sets, args = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                args.append(json.dumps(v) if k == "data" else v)
        if not sets:
            return self.get(ident)
        with self._lock:
            self._db.execute(f"UPDATE items SET {', '.join(sets)}, updated=? WHERE id=?",
                             (*args, self._clock(), ident))
            self._db.commit()
        return self.get(ident)

    def complete(self, ident: int) -> Item | None:
        item = self.get(ident)
        if item is None:
            return None
        prior = json.dumps({"id": item.id, "due": item.due, "status": item.status, "notified": item.notified})
        now = self._clock()
        if item.every_days:
            step = item.every_days * DAY
            nxt = (item.due if item.due is not None else now) + step
            while nxt <= now:
                nxt += step
            done = self.update(ident, due=nxt, notified=None)
        else:
            done = self.update(ident, status="done")
        self.log_undo(f"ticked off {item.name}", "butler.restore", prior)
        return done

    def reopen(self, ident: int) -> Item | None:
        return self.update(ident, status="open", notified=None)

    def mark_notified(self, ident: int, now: float | None = None) -> None:
        self.update(ident, notified=now if now is not None else self._clock())

    def delete(self, ident: int) -> bool:
        with self._lock:
            n = self._db.execute("DELETE FROM items WHERE id=?", (ident,)).rowcount
            self._db.commit()
        return bool(n)

    def delete_domain(self, domain: str) -> int:
        with self._lock:
            n = self._db.execute("DELETE FROM items WHERE domain=?", (domain,)).rowcount
            self._db.commit()
        return n

    # ---- reading -------------------------------------------------------------------------------------
    def get(self, ident: int) -> Item | None:
        with self._lock:
            r = self._db.execute(f"SELECT {_COLS} FROM items WHERE id=?", (ident,)).fetchone()
        return _item(r) if r else None

    def list(self, domain: str, status: str = "open", limit: int = 100) -> list[Item]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_COLS} FROM items WHERE domain=? AND status=? "
                "ORDER BY due IS NULL, due, created, id LIMIT ?", (domain, status, limit)).fetchall()
        return [_item(r) for r in rows]

    def find(self, domain: str, text: str, status: str = "open") -> list[Item]:
        needle = " ".join(str(text).lower().split())
        if not needle:
            return []
        if needle.isdigit():
            hit = self.get(int(needle))
            if hit is not None and hit.domain == domain:
                return [hit]
        return [i for i in self.list(domain, status, limit=1000) if needle in i.name.lower()]

    def due(self, now: float, domain: str | None = None, renotify_s: float = DAY) -> list[Item]:
        sql = f"SELECT {_COLS} FROM items WHERE status='open' AND due IS NOT NULL AND due <= ?"
        args: list = [now]
        if domain:
            sql += " AND domain=?"
            args.append(domain)
        sql += " AND (notified IS NULL OR notified <= ?) ORDER BY due"
        args.append(now - renotify_s)
        with self._lock:
            return [_item(r) for r in self._db.execute(sql, args).fetchall()]

    def upcoming(self, now: float, horizon_s: float, domain: str | None = None) -> list[Item]:
        sql = f"SELECT {_COLS} FROM items WHERE status='open' AND due IS NOT NULL AND due > ? AND due <= ?"
        args: list = [now, now + horizon_s]
        if domain:
            sql += " AND domain=?"
            args.append(domain)
        with self._lock:
            return [_item(r) for r in self._db.execute(sql + " ORDER BY due", args).fetchall()]

    def export(self, domain: str) -> list[dict]:
        rows = []
        for status in ("open", "done"):
            rows += [asdict(i) for i in self.list(domain, status, limit=100_000)]
        return rows

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            r = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)", (key, value))
            self._db.commit()

    # ---- undo ----------------------------------------------------------------------------------------
    def log_undo(self, label: str, action: str, ref: str) -> int:
        with self._lock:
            cur = self._db.execute("INSERT INTO undo(ts, label, action, ref) VALUES (?,?,?,?)",
                                   (self._clock(), label, action, ref))
            self._db.commit()
            return cur.lastrowid

    def undo_entries(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT id, ts, label, action, ref FROM undo WHERE done=0 "
                                    "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "ts": r[1], "label": r[2], "action": r[3], "ref": r[4]} for r in rows]

    def apply_undo(self, undo_id: int) -> bool:
        with self._lock:
            r = self._db.execute("SELECT action, ref FROM undo WHERE id=? AND done=0", (undo_id,)).fetchone()
            if r is None:
                return False
            action, ref = r
            ok = False
            if action == "butler.restore":
                prior = json.loads(ref)
                ok = self.update(prior["id"], due=prior["due"], status=prior["status"],
                                 notified=prior["notified"]) is not None
            elif action == "butler.delete":
                ok = self.delete(int(ref))
            self._db.execute("UPDATE undo SET done=1 WHERE id=?", (undo_id,))
            self._db.commit()
        return ok
