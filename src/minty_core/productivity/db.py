"""One owner-only SQLite file for habits, people, trips, meals and alarms."""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path


class LifeDB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not self.path.exists()
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS habits (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
              every TEXT DEFAULT 'daily', created TEXT, key TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS checkins (habit_id INTEGER, day TEXT, UNIQUE(habit_id, day));
            CREATE TABLE IF NOT EXISTS people (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
              birthday TEXT DEFAULT '', created REAL, key TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY AUTOINCREMENT, person_id INTEGER, text TEXT,
              source TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS occasions (id INTEGER PRIMARY KEY AUTOINCREMENT, person_id INTEGER, who TEXT,
              what TEXT, md TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS trips (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, kind TEXT,
              start TEXT, days INTEGER, created REAL, key TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS packing (id INTEGER PRIMARY KEY AUTOINCREMENT, trip_id INTEGER, item TEXT,
              done INTEGER DEFAULT 0, UNIQUE(trip_id, item));
            CREATE TABLE IF NOT EXISTS meals (day TEXT, slot TEXT, name TEXT, ingredients TEXT DEFAULT '[]',
              UNIQUE(day, slot));
            CREATE TABLE IF NOT EXISTS shopping (id INTEGER PRIMARY KEY AUTOINCREMENT, item TEXT UNIQUE,
              qty TEXT DEFAULT '', done INTEGER DEFAULT 0, source TEXT DEFAULT 'manual');
            CREATE TABLE IF NOT EXISTS alarms (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, days TEXT DEFAULT '',
              label TEXT DEFAULT '', brief INTEGER DEFAULT 1, enabled INTEGER DEFAULT 1, last_day TEXT DEFAULT '',
              snoozed_until REAL, once INTEGER DEFAULT 0);
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
