"""One owner-only SQLite file for the whole study suite."""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path


class StudyDB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not self.path.exists()
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS cards (id INTEGER PRIMARY KEY AUTOINCREMENT, deck TEXT NOT NULL,
              front TEXT NOT NULL, back TEXT NOT NULL, source TEXT DEFAULT '', created REAL,
              stability REAL DEFAULT 0, difficulty REAL DEFAULT 0, due REAL, last_review REAL,
              reps INTEGER DEFAULT 0, lapses INTEGER DEFAULT 0, state TEXT DEFAULT 'new', key TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, card_id INTEGER, ts REAL,
              grade INTEGER, first INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS exams (id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, day TEXT,
              topics TEXT DEFAULT '[]', created REAL);
            CREATE TABLE IF NOT EXISTS questions (id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, topic TEXT,
              text TEXT, answer TEXT DEFAULT '', marks INTEGER DEFAULT 0, source TEXT DEFAULT '', year TEXT DEFAULT '',
              asked INTEGER DEFAULT 0, correct INTEGER DEFAULT 0, last_ts REAL, key TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS mistakes (id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, topic TEXT,
              what TEXT, fix TEXT, count INTEGER DEFAULT 1, created REAL, last_ts REAL, card_id INTEGER,
              key TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, kind TEXT,
              minutes REAL DEFAULT 0, detail TEXT DEFAULT '');
        """)
        self.db.commit()
        if fresh:
            os.chmod(self.path, 0o600)
