"""The rebuildable index over the vault's files: full-text search, links and tags. Delete it and it regrows."""
from __future__ import annotations

import re
import sqlite3
import threading
from collections import Counter
from pathlib import Path

from minty_core.vault.note import Note

_WORD = re.compile(r"\w+", re.UNICODE)
_LINK = re.compile(r"\]\(note:([0-9A-Za-z_\-]+)\)")


def words(text: str) -> list[str]:
    return [w for w in _WORD.findall(str(text).lower()) if len(w) > 1 or w.isdigit()]


def links_in(body: str) -> list[str]:
    seen: list[str] = []
    for m in _LINK.finditer(body or ""):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


class Index:
    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript(
                "CREATE TABLE IF NOT EXISTS notes (id TEXT UNIQUE, path TEXT UNIQUE, title TEXT, created TEXT,"
                " updated TEXT, source TEXT, confidence TEXT, tags TEXT, project TEXT, archived INTEGER,"
                " mtime REAL, size INTEGER);"
                "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(title, body, tags,"
                " tokenize='porter unicode61');"
                "CREATE TABLE IF NOT EXISTS links (src TEXT, dst TEXT);"
                "CREATE INDEX IF NOT EXISTS links_dst ON links(dst);")
            self._db.commit()

    # ---- writing -------------------------------------------------------------------------------------
    def upsert(self, note: Note, mtime: float) -> None:
        archived = int(note.path.startswith("_archive/"))
        with self._lock:
            self._drop(note.id, note.path)
            cur = self._db.execute(
                "INSERT INTO notes(id, path, title, created, updated, source, confidence, tags, project, archived,"
                " mtime, size) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (note.id, note.path, note.title, note.created, note.updated, note.source, note.confidence,
                 ", ".join(note.tags), note.project, archived, mtime, len(note.body)))
            self._db.execute("INSERT INTO notes_fts(rowid, title, body, tags) VALUES (?,?,?,?)",
                             (cur.lastrowid, note.title, note.body, " ".join(note.tags)))
            for dst in links_in(note.body):
                self._db.execute("INSERT INTO links(src, dst) VALUES (?,?)", (note.id, dst))
            self._db.commit()

    def _drop(self, ident: str, path: str) -> None:
        for (rowid, old_id) in self._db.execute("SELECT rowid, id FROM notes WHERE id=? OR path=?",
                                                (ident, path)).fetchall():
            self._db.execute("DELETE FROM notes_fts WHERE rowid=?", (rowid,))
            self._db.execute("DELETE FROM links WHERE src=?", (old_id,))
            self._db.execute("DELETE FROM notes WHERE rowid=?", (rowid,))

    def drop_path(self, path: str) -> None:
        with self._lock:
            self._drop("", path)
            self._db.commit()

    # ---- reading -------------------------------------------------------------------------------------
    def mtimes(self) -> dict[str, float]:
        with self._lock:
            return {p: m for p, m in self._db.execute("SELECT path, mtime FROM notes").fetchall()}

    def row(self, ident: str) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT id, path, title, created, updated, source, confidence, tags, project,"
                                 " archived FROM notes WHERE id=?", (ident,)).fetchone()
        return self._as_dict(r)

    @staticmethod
    def _as_dict(r) -> dict | None:
        if r is None:
            return None
        keys = ("id", "path", "title", "created", "updated", "source", "confidence", "tags", "project", "archived")
        d = dict(zip(keys, r))
        d["tags"] = [t.strip() for t in d["tags"].split(",") if t.strip()]
        d["archived"] = bool(d["archived"])
        return d

    def rows(self, folder: str | None = None, include_archived: bool = False, limit: int = 200) -> list[dict]:
        sql = ("SELECT id, path, title, created, updated, source, confidence, tags, project, archived FROM notes"
               " WHERE 1=1")
        args: list = []
        if not include_archived:
            sql += " AND archived=0"
        if folder:
            sql += " AND path LIKE ?"
            args.append(folder.rstrip("/") + "/%")
        sql += " ORDER BY updated DESC, created DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            return [self._as_dict(r) for r in self._db.execute(sql, args).fetchall()]

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM notes WHERE archived=0").fetchone()[0]

    def search(self, query: str, limit: int = 10, include_archived: bool = False) -> list[tuple[str, float]]:
        terms: list[str] = []
        for w in words(query)[:12]:
            if w not in terms:
                terms.append(w)
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)  # quoted: what you say never becomes search syntax
        sql = ("SELECT n.id, bm25(notes_fts, 3.0, 1.0, 2.0) FROM notes_fts JOIN notes n ON n.rowid = notes_fts.rowid"
               " WHERE notes_fts MATCH ?" + ("" if include_archived else " AND n.archived = 0")
               + " ORDER BY bm25(notes_fts, 3.0, 1.0, 2.0) LIMIT ?")
        with self._lock:
            try:
                return [(i, -r) for i, r in self._db.execute(sql, (match, limit)).fetchall()]
            except sqlite3.OperationalError:
                return []

    def vocabulary(self) -> tuple[Counter, Counter]:
        """(tags, projects) in use, with how many live notes use each."""
        tags: Counter = Counter()
        projects: Counter = Counter()
        with self._lock:
            for t, p in self._db.execute("SELECT tags, project FROM notes WHERE archived=0").fetchall():
                tags.update(x.strip() for x in t.split(",") if x.strip())
                if p.strip():
                    projects[p.strip()] += 1
        return tags, projects

    def backlinks(self, ident: str) -> list[str]:
        with self._lock:
            return [r[0] for r in self._db.execute(
                "SELECT DISTINCT l.src FROM links l JOIN notes n ON n.id = l.src WHERE l.dst=? AND n.archived=0",
                (ident,)).fetchall()]

    def broken_links(self) -> list[tuple[str, str]]:
        with self._lock:
            return [(s, d) for s, d in self._db.execute(
                "SELECT l.src, l.dst FROM links l LEFT JOIN notes n ON n.id = l.dst WHERE n.id IS NULL"
                " ORDER BY l.src, l.dst").fetchall()]
