"""Snippet vault (653): small pieces of code or commands saved by name, found by words. A snippet that looks like it holds a
key or password is refused, so the vault never becomes a place secrets leak from."""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

from minty_core.secretscan import SecretScanner


class DevDB:
    """One owner-only SQLite file shared by snippets and ideas."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not self.path.exists()
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS snippets (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
              key TEXT UNIQUE, language TEXT DEFAULT '', tags TEXT DEFAULT '', text TEXT NOT NULL, created REAL, updated REAL);
            CREATE TABLE IF NOT EXISTS ideas (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, project TEXT DEFAULT '',
              text TEXT NOT NULL, status TEXT DEFAULT 'open', created REAL, closed REAL);
        """)
        if "closed" not in {r["name"] for r in self.db.execute("PRAGMA table_info(ideas)").fetchall()}:
            self.db.execute("ALTER TABLE ideas ADD COLUMN closed REAL")
        self.db.commit()
        if fresh:
            os.chmod(self.path, 0o600)


class Snippets:
    def __init__(self, path: Path | DevDB, clock: Callable[[], float] = time.time, max_snippets: int = 500,
                 max_chars: int = 8000):
        self.d = path if isinstance(path, DevDB) else DevDB(path)
        self._clock, self.max_snippets, self.max_chars = clock, max_snippets, max_chars
        self._scan = SecretScanner()

    @staticmethod
    def _key(name: str) -> str:
        return " ".join(str(name).lower().split())[:80]

    def add(self, name: str, text: str, language: str = "", tags: str = "") -> int:
        key = self._key(name)
        if not key or not str(text).strip():
            raise ValueError("A snippet needs a name and some text.")
        if len(text) > self.max_chars:
            raise ValueError(f"That snippet is too long (the limit is {self.max_chars} characters).")
        if any(f.severity in ("secret", "alarm") for f in self._scan.scan(text)):
            raise ValueError("That looks like a key or password, so I did not save it.")
        now = self._clock()
        with self.d.lock:
            row = self.d.db.execute("SELECT id FROM snippets WHERE key=?", (key,)).fetchone()
            if row:
                self.d.db.execute("UPDATE snippets SET text=?, language=?, tags=?, updated=? WHERE id=?",
                                  (text, language[:20], tags[:100], now, row["id"]))
                self.d.db.commit()
                return row["id"]
            if self.d.db.execute("SELECT COUNT(*) FROM snippets").fetchone()[0] >= self.max_snippets:
                raise ValueError(f"The snippet vault is full ({self.max_snippets}); delete some first.")
            cur = self.d.db.execute("INSERT INTO snippets(name, key, language, tags, text, created, updated) "
                                    "VALUES (?,?,?,?,?,?,?)", (" ".join(name.split())[:80], key, language[:20],
                                                              tags[:100], text, now, now))
            self.d.db.commit()
            return cur.lastrowid

    def get(self, name: str) -> dict | None:
        with self.d.lock:
            row = self.d.db.execute("SELECT * FROM snippets WHERE key=?", (self._key(name),)).fetchone()
        return dict(row) if row else None

    def list(self) -> list[dict]:
        with self.d.lock:
            return [dict(r) for r in self.d.db.execute("SELECT id, name, language, tags FROM snippets ORDER BY name").fetchall()]

    def find(self, query: str, limit: int = 8) -> list[dict]:
        words = [w for w in re.findall(r"[\w.+-]+", str(query).lower()) if len(w) > 1]
        if not words:
            return []
        out = []
        for row in self.d.db.execute("SELECT * FROM snippets ORDER BY updated DESC").fetchall():
            hay = f"{row['name']} {row['tags']} {row['language']} {row['text']}".lower()
            score = sum(hay.count(w) for w in words) + 3 * sum(w in row["name"].lower() for w in words)
            if score and all(w in hay for w in words):
                out.append((score, dict(row)))
        return [r for _, r in sorted(out, key=lambda x: -x[0])[:limit]]

    def delete(self, name: str) -> bool:
        with self.d.lock:
            n = self.d.db.execute("DELETE FROM snippets WHERE key=?", (self._key(name),)).rowcount
            self.d.db.commit()
        return bool(n)
