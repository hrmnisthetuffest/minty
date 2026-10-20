"""Read-later queue (564): pages saved by address, fetched as plain text through safe-open when asked, summarised with the
extractive summariser (sentences from the page, nothing invented)."""
from __future__ import annotations

import time
from typing import Awaitable, Callable

from minty_core.study.text import summarise
from minty_core.web.db import WebDB
from minty_core.web.safeopen import Opened
from minty_core.web.urls import check_web_url

MAX_TEXT = 20000


class ReadLater:
    def __init__(self, db: WebDB, clock: Callable[[], float] = time.time):
        self.db, self._clock = db, clock

    def add(self, url: str, title: str = "") -> int:
        url = check_web_url(url)
        with self.db.lock:
            row = self.db.db.execute("SELECT id FROM readlater WHERE url=?", (url,)).fetchone()
            if row:
                return row["id"]
            cur = self.db.db.execute("INSERT INTO readlater(url, title, added) VALUES (?,?,?)",
                                     (url, " ".join(str(title).split())[:160], self._clock()))
            self.db.db.commit()
            return cur.lastrowid

    def get(self, ident: int) -> dict | None:
        with self.db.lock:
            row = self.db.db.execute("SELECT * FROM readlater WHERE id=?", (ident,)).fetchone()
        return dict(row) if row else None

    def list(self, status: str = "unread") -> list[dict]:
        with self.db.lock:
            return [dict(r) for r in self.db.db.execute(
                "SELECT id, url, title, added, status, summary FROM readlater WHERE status=? ORDER BY id",
                (status,)).fetchall()]

    def next(self) -> dict | None:
        rows = self.list()
        return self.get(rows[0]["id"]) if rows else None

    def mark_read(self, ident: int) -> bool:
        with self.db.lock:
            n = self.db.db.execute("UPDATE readlater SET status='read' WHERE id=?", (ident,)).rowcount
            self.db.db.commit()
        return bool(n)

    def remove(self, ident: int) -> bool:
        with self.db.lock:
            n = self.db.db.execute("DELETE FROM readlater WHERE id=?", (ident,)).rowcount
            self.db.db.commit()
        return bool(n)

    async def fetch(self, ident: int, opener: Callable[[str], Awaitable[Opened]]) -> dict:
        row = self.get(ident)
        if row is None:
            raise ValueError("I have no such page in the queue.")
        got = await opener(row["url"])
        if got.opened:
            text = got.text[:MAX_TEXT]
            points = summarise(text, 4).points
            summary = " ".join(points) if points else text[:400]
            title = got.title or row["title"]
        else:
            text, title = "", row["title"]
            summary = "Could not read it: " + (got.note or " ".join(got.report.reasons) or "unknown reason")
        with self.db.lock:
            self.db.db.execute("UPDATE readlater SET title=?, text=?, summary=? WHERE id=?",
                               (title, text, summary, ident))
            self.db.db.commit()
        return self.get(ident)
