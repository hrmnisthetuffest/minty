"""One owner-only SQLite file for the integrations: calendar feeds and events, the local-network inventory, weather alerts already said."""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path


class IntegrationsDB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not self.path.exists()
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS cal_feeds (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, url TEXT, imported REAL);
            CREATE TABLE IF NOT EXISTS cal_events (feed_id INTEGER, key TEXT, data TEXT, hash TEXT, UNIQUE(feed_id, key));
            CREATE TABLE IF NOT EXISTS lan_devices (mac TEXT PRIMARY KEY, ip TEXT, first_seen REAL, last_seen REAL,
              label TEXT DEFAULT '', known INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS seen_alerts (key TEXT PRIMARY KEY, at REAL);
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
