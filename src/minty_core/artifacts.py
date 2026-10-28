"""The sheets and docs Minty made or was told about.

Two jobs. Work that builds on earlier work (a lead list, a research table) finds its sheet again instead of making a second one,
and the places Minty may write to without a question are exactly these: a sheet or doc Minty created itself, or one you named in your
own words. A model cannot add to the list by saying so; `add` is called by code that saw Composio's answer to a create, or by a tool
that the user's own words authorise.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

KINDS = ("sheet", "doc")
ID = re.compile(r"[A-Za-z0-9_-]{20,100}")
_LINK = re.compile(r"https://docs\.google\.com/(spreadsheets|document)/d/([A-Za-z0-9_-]{20,100})(?![A-Za-z0-9_-])")
_KIND = {"spreadsheets": "sheet", "document": "doc"}


@dataclass(frozen=True)
class Artifact:
    id: int
    kind: str
    ext_id: str
    title: str
    url: str
    key: str  # what it is for: a project or task slug, so the next piece of work on it finds it
    source: str  # minty (it made it) | user (you named it)
    status: str  # active | stale
    created: float
    updated: float
    note: str = ""


class ArtifactStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.close(os.open(path, os.O_CREAT | os.O_RDWR, 0o600))
        self._clock = clock
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS artifacts (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, ext_id TEXT NOT NULL UNIQUE, "
            "title TEXT NOT NULL, url TEXT NOT NULL DEFAULT '', key TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'minty', "
            "status TEXT NOT NULL DEFAULT 'active', created REAL NOT NULL, updated REAL NOT NULL, note TEXT NOT NULL DEFAULT '')")
        self._db.commit()

    @staticmethod
    def parse_ref(ref: str) -> tuple[str | None, str]:
        """(kind, id) from a Google Docs link, (None, id) from a plain id, (None, '') from anything else."""
        text = str(ref or "").strip()
        link = _LINK.match(text)
        if link:
            return _KIND[link.group(1)], link.group(2)
        return (None, text) if ID.fullmatch(text) else (None, "")

    def add(self, kind: str, ext_id: str, title: str, url: str = "", key: str = "", source: str = "minty", note: str = "") -> Artifact:
        if kind not in KINDS:
            raise ValueError(f"An artifact is one of {', '.join(KINDS)}.")
        if not ID.fullmatch(str(ext_id or "")):
            raise ValueError("That is not a Google sheet or doc id.")
        now = self._clock()
        with self._lock:
            self._db.execute(
                "INSERT INTO artifacts (kind, ext_id, title, url, key, source, status, created, updated, note) VALUES (?,?,?,?,?,?, 'active', ?,?,?) "
                "ON CONFLICT(ext_id) DO UPDATE SET title = excluded.title, url = CASE WHEN excluded.url != '' THEN excluded.url ELSE url END, "
                "key = CASE WHEN excluded.key != '' THEN excluded.key ELSE key END, status = 'active', updated = excluded.updated",
                (kind, ext_id, str(title)[:160], str(url)[:300], str(key)[:60], source if source in ("minty", "user") else "minty", now, now, str(note)[:200]))
            self._db.commit()
        return self.get(ext_id)

    def get(self, ext_id: str) -> Artifact | None:
        row = self._db.execute("SELECT * FROM artifacts WHERE ext_id = ?", (ext_id,)).fetchone()
        return self._row(row) if row else None

    def known(self, ext_id: str) -> bool:
        return self.get(str(ext_id)) is not None

    def find(self, kind: str | None = None, key: str | None = None, title: str | None = None) -> list[Artifact]:
        rows = self._db.execute("SELECT * FROM artifacts ORDER BY updated DESC, id DESC").fetchall()
        out = [self._row(r) for r in rows]
        if kind:
            out = [a for a in out if a.kind == kind]
        if key:
            out = [a for a in out if a.key == key]
        if title:
            out = [a for a in out if a.title.lower() == title.strip().lower()]
        return out

    def tag(self, ext_id: str, key: str) -> bool:
        """Say what an artifact is for, so the next piece of work on that subject finds it."""
        with self._lock:
            cur = self._db.execute("UPDATE artifacts SET key = ?, updated = ? WHERE ext_id = ?", (str(key)[:60], self._clock(), ext_id))
            self._db.commit()
        return cur.rowcount > 0

    def forget(self, ext_id: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM artifacts WHERE ext_id = ?", (ext_id,))
            self._db.commit()
        return cur.rowcount > 0

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _row(r) -> Artifact:
        return Artifact(*r)
