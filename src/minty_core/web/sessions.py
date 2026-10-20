"""Browser sessions saved by name and restored on request (565): just the addresses of the open tabs."""
from __future__ import annotations

import json
import time
from typing import Callable

from minty_core.web.db import WebDB
from minty_core.web.urls import check_web_url

MAX_TABS = 40


class Sessions:
    def __init__(self, db: WebDB, clock: Callable[[], float] = time.time):
        self.db, self._clock = db, clock

    @staticmethod
    def _key(name: str) -> str:
        return " ".join(str(name).lower().split())[:60]

    def save(self, name: str, urls: list[str]) -> dict:
        key = self._key(name)
        if not key:
            raise ValueError("Give the session a name.")
        clean: list[str] = []
        for u in urls:
            try:
                good = check_web_url(u)
            except ValueError:
                continue
            if good not in clean:
                clean.append(good)
        clean = clean[:MAX_TABS]
        with self.db.lock:
            row = self.db.db.execute("SELECT id FROM sessions WHERE name=?", (key,)).fetchone()
            if row:
                self.db.db.execute("UPDATE sessions SET ts=?, urls=? WHERE id=?", (self._clock(), json.dumps(clean), row["id"]))
            else:
                self.db.db.execute("INSERT INTO sessions(name, ts, urls) VALUES (?,?,?)",
                                   (key, self._clock(), json.dumps(clean)))
            self.db.db.commit()
        return self.get(key)

    def _row(self, row) -> dict:
        return {"name": row["name"], "ts": row["ts"], "urls": json.loads(row["urls"])}

    def get(self, name: str) -> dict | None:
        with self.db.lock:
            row = self.db.db.execute("SELECT * FROM sessions WHERE name=?", (self._key(name),)).fetchone()
        return self._row(row) if row else None

    def list(self) -> list[dict]:
        with self.db.lock:
            return [self._row(r) for r in self.db.db.execute("SELECT * FROM sessions ORDER BY id").fetchall()]

    def latest(self) -> dict | None:
        with self.db.lock:
            row = self.db.db.execute("SELECT * FROM sessions ORDER BY ts DESC, id DESC LIMIT 1").fetchone()
        return self._row(row) if row else None

    def delete(self, name: str) -> bool:
        with self.db.lock:
            n = self.db.db.execute("DELETE FROM sessions WHERE name=?", (self._key(name),)).rowcount
            self.db.db.commit()
        return bool(n)
