"""One owner-only SQLite file for watches, the read-later queue, quarantined downloads, the form log and sessions."""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path


class WebDB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not self.path.exists()
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS watches (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL,
              kind TEXT NOT NULL, label TEXT DEFAULT '', hours REAL NOT NULL, target REAL, wish INTEGER DEFAULT 0,
              next_check REAL DEFAULT 0, last_check REAL, fails INTEGER DEFAULT 0, last_price REAL,
              last_stock INTEGER, last_hash TEXT DEFAULT '', last_text TEXT DEFAULT '', notified_price REAL,
              currency TEXT DEFAULT '', created REAL, key TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS readlater (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT UNIQUE, title TEXT,
              added REAL, status TEXT DEFAULT 'unread', text TEXT DEFAULT '', summary TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS downloads (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, sha256 TEXT,
              size INTEGER, source TEXT DEFAULT '', path TEXT, status TEXT DEFAULT 'quarantined', vt TEXT DEFAULT '',
              created REAL);
            CREATE TABLE IF NOT EXISTS formlog (id INTEGER PRIMARY KEY AUTOINCREMENT, host TEXT, field TEXT, ts REAL);
            CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, ts REAL,
              urls TEXT);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        """)
        self.db.commit()
        if fresh:
            os.chmod(self.path, 0o600)

    def meta(self, key: str, default: str = "") -> str:
        with self.lock:
            row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self.lock:
            self.db.execute("INSERT INTO meta(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, value))
            self.db.commit()

    def close(self) -> None:
        with self.lock:
            self.db.close()
