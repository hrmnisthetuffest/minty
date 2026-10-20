"""One owner-only SQLite file for the creation helpers: feed cache, paper topics and what was seen, derived notes with their
sources, and the idea inbox (the same tables as the developer ideas)."""
from __future__ import annotations

from minty_core.dev.snippets import DevDB


class CreateDB(DevDB):
    def __init__(self, path):
        super().__init__(path)
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS feed_cache (url TEXT PRIMARY KEY, fetched REAL, body TEXT);
                CREATE TABLE IF NOT EXISTS topics (topic TEXT PRIMARY KEY, created REAL, last_check REAL);
                CREATE TABLE IF NOT EXISTS seen (topic TEXT, paper_id TEXT, UNIQUE(topic, paper_id));
                CREATE TABLE IF NOT EXISTS derived (id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT UNIQUE, title TEXT,
                  kind TEXT, created REAL, flagged TEXT DEFAULT '');
                CREATE TABLE IF NOT EXISTS derived_sources (note TEXT, source TEXT, sha TEXT, kind TEXT);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS feeds (url TEXT PRIMARY KEY, added REAL);
                CREATE TABLE IF NOT EXISTS glossary (term TEXT NOT NULL COLLATE NOCASE, lang TEXT NOT NULL,
                  translation TEXT NOT NULL, UNIQUE(term, lang));
            """)
            self.db.commit()

    def meta(self, key: str, default: str = "") -> str:
        with self.lock:
            row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self.lock:
            self.db.execute("INSERT INTO meta(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, value))
            self.db.commit()
