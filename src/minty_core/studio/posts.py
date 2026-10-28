"""The post queue: every video or carousel drafted for publishing, and exactly what happened to it.

A post is claimed before it is published (drafted or failed -> publishing, once, atomically), so it cannot go out twice. A publish that
was cut off after the post may have gone out is `unknown`: it is reported and never repeated by itself. The same content (by hash) is
refused if it is already queued, being published, unknown, or posted.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

DRAFTED, PUBLISHING, POSTED, FAILED, UNKNOWN, DISCARDED = "drafted", "publishing", "posted", "failed", "unknown", "discarded"
DAY_S = 86400
_COLS = ("id, created, kind, files, caption, status, how, content_hash, problems, licensed, media_id, permalink, error, updated, ai, tags, insights")


class DuplicatePost(Exception):
    pass


@dataclass(frozen=True)
class Post:
    id: int
    created: float
    kind: str  # reel | carousel
    files: list
    caption: str
    status: str
    how: str  # auto | approved | ''
    content_hash: str
    problems: list  # what the checks found wrong with the files at drafting time
    licensed: bool  # every file has a clean licence on record
    media_id: str
    permalink: str
    error: str
    updated: float
    ai: bool = True  # labelled as AI-generated when published
    tags: str = ""  # what kind of post (comma separated), so what works can be seen
    insights: dict = None  # how it did, as Instagram reports (reach, likes ...)

    def __post_init__(self):
        object.__setattr__(self, "insights", self.insights or {})


class PostStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock, self._lock = clock, threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS posts (id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL NOT NULL, kind TEXT NOT NULL,"
            " files TEXT NOT NULL, caption TEXT NOT NULL, status TEXT NOT NULL, how TEXT NOT NULL DEFAULT '',"
            " content_hash TEXT NOT NULL, problems TEXT NOT NULL DEFAULT '[]', licensed INTEGER NOT NULL DEFAULT 0,"
            " media_id TEXT NOT NULL DEFAULT '', permalink TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',"
            " updated REAL NOT NULL, ai INTEGER NOT NULL DEFAULT 1, tags TEXT NOT NULL DEFAULT '', insights TEXT NOT NULL DEFAULT '{}')")
        self._db.execute("UPDATE posts SET status=?, error=?, updated=? WHERE status=?",
                         (UNKNOWN, "the program stopped while this was being published", self._clock(), PUBLISHING))
        self._db.commit()

    @staticmethod
    def _post(r) -> Post:
        return Post(r[0], r[1], r[2], json.loads(r[3]), r[4], r[5], r[6], r[7], json.loads(r[8]), bool(r[9]), r[10], r[11], r[12], r[13],
                    bool(r[14]), r[15], json.loads(r[16] or "{}"))

    def add(self, *, kind: str, files: list[str], caption: str, content_hash: str, problems: list[str], licensed: bool,
            ai: bool = True, tags: str = "") -> Post:
        now = self._clock()
        with self._lock:
            row = self._db.execute("SELECT id FROM posts WHERE content_hash=? AND status NOT IN (?, ?)",
                                   (content_hash, DISCARDED, FAILED)).fetchone()
            if row:
                raise DuplicatePost(f"this is already queued or posted as post {row[0]}")
            cur = self._db.execute(
                "INSERT INTO posts(created, kind, files, caption, status, content_hash, problems, licensed, updated, ai, tags)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)", (now, kind, json.dumps(files), caption, DRAFTED, content_hash,
                                                     json.dumps(problems), int(licensed), now, int(ai), tags[:200]))
            self._db.commit()
        return self.get(cur.lastrowid)

    def get(self, ident: int) -> Post | None:
        row = self._db.execute(f"SELECT {_COLS} FROM posts WHERE id=?", (int(ident),)).fetchone()
        return self._post(row) if row else None

    def recent(self, limit: int = 10) -> list[Post]:
        rows = self._db.execute(f"SELECT {_COLS} FROM posts WHERE status!=? ORDER BY id DESC LIMIT ?", (DISCARDED, int(limit))).fetchall()
        return [self._post(r) for r in rows]

    def claim(self, ident: int, how: str) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE posts SET status=?, how=?, updated=? WHERE id=? AND status IN (?,?)",
                                   (PUBLISHING, how, self._clock(), int(ident), DRAFTED, FAILED))
            self._db.commit()
            return cur.rowcount == 1

    def finish(self, ident: int, status: str, *, media_id: str = "", permalink: str = "", error: str = "") -> None:
        with self._lock:
            self._db.execute("UPDATE posts SET status=?, media_id=?, permalink=?, error=?, updated=? WHERE id=?",
                             (status, media_id, permalink, error[:300], self._clock(), int(ident)))
            self._db.commit()

    def discard(self, ident: int) -> bool:
        with self._lock:
            cur = self._db.execute("UPDATE posts SET status=?, updated=? WHERE id=? AND status IN (?,?,?)",
                                   (DISCARDED, self._clock(), int(ident), DRAFTED, FAILED, UNKNOWN))
            self._db.commit()
            return cur.rowcount == 1

    def posted_today(self, how: str | None = None) -> int:
        sql, args = "SELECT COUNT(*) FROM posts WHERE status=? AND updated>=?", [POSTED, self._clock() - DAY_S]
        if how:
            sql, args = sql + " AND how=?", args + [how]
        return self._db.execute(sql, args).fetchone()[0]

    def hours_since_last_post(self) -> float | None:
        row = self._db.execute("SELECT MAX(updated) FROM posts WHERE status=?", (POSTED,)).fetchone()
        return None if row[0] is None else (self._clock() - row[0]) / 3600.0

    def track_record(self) -> dict[str, int]:
        approved = self._db.execute("SELECT COUNT(*) FROM posts WHERE status=? AND how='approved'", (POSTED,)).fetchone()[0]
        bad = self._db.execute("SELECT COUNT(*) FROM posts WHERE status IN (?,?)", (FAILED, UNKNOWN)).fetchone()[0]
        return {"approved_posts": approved, "bad": bad}

    def set_insights(self, ident: int, numbers: dict) -> None:
        with self._lock:
            self._db.execute("UPDATE posts SET insights=? WHERE id=?", (json.dumps(numbers), int(ident)))
            self._db.commit()

    def posted_with_media(self) -> list[Post]:
        rows = self._db.execute(f"SELECT {_COLS} FROM posts WHERE status=? AND media_id!='' ORDER BY id", (POSTED,)).fetchall()
        return [self._post(r) for r in rows]
