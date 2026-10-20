"""Which form field went to which site, and when (574). The value is never recorded: only the field name and the host."""
from __future__ import annotations

import time
from typing import Callable

from minty_core.web.db import WebDB


class FormLog:
    def __init__(self, db: WebDB, clock: Callable[[], float] = time.time):
        self.db, self._clock = db, clock

    def record(self, host: str, field: str) -> None:
        host, field = str(host).lower().strip()[:120], str(field).lower().strip()[:40]
        if not host or not field:
            return
        with self.db.lock:
            self.db.db.execute("INSERT INTO formlog(host, field, ts) VALUES (?,?,?)", (host, field, self._clock()))
            self.db.db.commit()

    def seen(self, host: str) -> list[dict]:
        with self.db.lock:
            rows = self.db.db.execute("SELECT field, COUNT(*) n, MAX(ts) last FROM formlog WHERE host=? "
                                      "GROUP BY field ORDER BY field", (str(host).lower().strip(),)).fetchall()
        return [{"field": r["field"], "count": r["n"], "last": r["last"]} for r in rows]

    def sites(self) -> list[dict]:
        with self.db.lock:
            rows = self.db.db.execute("SELECT host, field, COUNT(*) n FROM formlog GROUP BY host, field "
                                      "ORDER BY host, field").fetchall()
        out: dict[str, dict] = {}
        for r in rows:
            site = out.setdefault(r["host"], {"host": r["host"], "fields": [], "count": 0})
            site["fields"].append(r["field"])
            site["count"] += r["n"]
        return list(out.values())

    def forget(self, host: str) -> int:
        with self.db.lock:
            n = self.db.db.execute("DELETE FROM formlog WHERE host=?", (str(host).lower().strip(),)).rowcount
            self.db.db.commit()
        return n

    def forget_all(self) -> int:
        with self.db.lock:
            n = self.db.db.execute("DELETE FROM formlog").rowcount
            self.db.db.commit()
        return n
