"""The school inbox: a homework ledger and a notice list, fed by the portal check, notifications or by hand.

Everything that comes from a web page is untrusted text. It is parsed with a strict line format, cut to short
lengths, and only ever stored and read back: no value from here is used as a link, a path or a command.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from minty_core.datewords import find_date
from minty_core.policy import safe_text

MAX_PER_CHECK = 30
_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Homework:
    id: int
    subject: str
    task: str
    due: date | None
    source: str
    done: bool
    key: str


@dataclass(frozen=True)
class Notice:
    id: int
    title: str
    body: str
    day: date | None
    source: str
    seen: bool


@dataclass
class Parsed:
    homework: list[tuple[str, str, date | None]]
    notices: list[tuple[str, date | None, str]]
    login_required: bool = False


def _key(*parts: str) -> str:
    text = "|".join(" ".join(_WORD.findall(str(p).lower())) for p in parts)
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def _when(text: str, today: date) -> date | None:
    text = (text or "").strip()
    if not text or text.lower() in ("none", "n/a", "-", "unknown"):
        return None
    return find_date(text, today)


def parse_portal_lines(text: str, today: date) -> Parsed:
    """Strict: only lines that start HOMEWORK | / NOTICE | count. Everything else is ignored."""
    out = Parsed([], [])
    if "LOGIN_REQUIRED" in (text or ""):
        out.login_required = True
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("-*• ").strip()
        kind, _, rest = line.partition("|")
        kind = kind.strip().upper()
        fields = [f.strip() for f in rest.split("|")]
        if kind == "HOMEWORK" and len(fields) >= 2 and len(out.homework) < MAX_PER_CHECK:
            subject, task = safe_text(fields[0], 40), safe_text(fields[1], 160)
            if subject and task:
                out.homework.append((subject, task, _when(fields[2], today) if len(fields) > 2 else None))
        elif kind == "NOTICE" and len(fields) >= 1 and len(out.notices) < MAX_PER_CHECK:
            title = safe_text(fields[0], 80)
            if title:
                out.notices.append((title, _when(fields[1], today) if len(fields) > 1 else None,
                                    safe_text(" ".join(fields[2:]), 200)))
    return out


class Inbox:
    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(
                "CREATE TABLE IF NOT EXISTS homework (id INTEGER PRIMARY KEY, subject TEXT, task TEXT, due TEXT,"
                " source TEXT, done INTEGER DEFAULT 0, key TEXT UNIQUE, added REAL, done_at REAL);"
                "CREATE TABLE IF NOT EXISTS notices (id INTEGER PRIMARY KEY, title TEXT, body TEXT, day TEXT,"
                " source TEXT, seen INTEGER DEFAULT 0, key TEXT UNIQUE, added REAL);")
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- homework ---------------------------------------------------------------------------------------
    def add_homework(self, subject: str, task: str, due: date | None, source: str = "portal",
                     now: datetime | None = None) -> tuple[Homework, bool]:
        key = _key(subject, task, due.isoformat() if due else "")
        with self._lock:
            row = self._db.execute("SELECT id FROM homework WHERE key=?", (key,)).fetchone()
            new = row is None
            if new:
                self._db.execute("INSERT INTO homework(subject, task, due, source, key, added) VALUES (?,?,?,?,?,?)",
                                 (subject, task, due.isoformat() if due else None, source, key,
                                  (now or datetime.now()).timestamp()))
                self._db.commit()
        return self._by_key(key), new

    def _by_key(self, key: str) -> Homework:
        with self._lock:
            r = self._db.execute("SELECT id, subject, task, due, source, done, key FROM homework WHERE key=?",
                                 (key,)).fetchone()
        return self._hw(r)

    @staticmethod
    def _hw(r) -> Homework:
        return Homework(r[0], r[1], r[2], date.fromisoformat(r[3]) if r[3] else None, r[4], bool(r[5]), r[6])

    def open_homework(self) -> list[Homework]:
        with self._lock:
            rows = self._db.execute("SELECT id, subject, task, due, source, done, key FROM homework WHERE done=0"
                                    " ORDER BY due IS NULL, due, id").fetchall()
        return [self._hw(r) for r in rows]

    def due_within(self, today: date, days: int = 1) -> list[Homework]:
        last = today + timedelta(days=days)
        return [h for h in self.open_homework() if h.due is not None and h.due <= last]

    def mark_done(self, words: str, now: datetime | None = None) -> list[Homework]:
        """Mark the one open item whose subject+task contains all the given words. Several matches: none is
        marked and the candidates are returned, so the caller can ask which."""
        wanted = [w for w in _WORD.findall(words.lower()) if w not in ("homework", "done", "the", "my", "is", "mark")]
        if not wanted:
            return []
        hits = [h for h in self.open_homework() if all(w in " ".join(_WORD.findall(
            f"{h.subject} {h.task}".lower())) for w in wanted)]
        if len(hits) == 1:
            with self._lock:
                self._db.execute("UPDATE homework SET done=1, done_at=? WHERE id=?",
                                 ((now or datetime.now()).timestamp(), hits[0].id))
                self._db.commit()
            return [Homework(hits[0].id, hits[0].subject, hits[0].task, hits[0].due, hits[0].source, True,
                             hits[0].key)]
        return hits

    # -- notices ----------------------------------------------------------------------------------------
    def add_notice(self, title: str, body: str, day: date | None, source: str = "portal",
                   now: datetime | None = None) -> tuple[Notice, bool]:
        key = _key(title, day.isoformat() if day else "")
        with self._lock:
            row = self._db.execute("SELECT id FROM notices WHERE key=?", (key,)).fetchone()
            new = row is None
            if new:
                self._db.execute("INSERT INTO notices(title, body, day, source, key, added) VALUES (?,?,?,?,?,?)",
                                 (title, body, day.isoformat() if day else None, source, key,
                                  (now or datetime.now()).timestamp()))
                self._db.commit()
            r = self._db.execute("SELECT id, title, body, day, source, seen FROM notices WHERE key=?",
                                 (key,)).fetchone()
        return Notice(r[0], r[1], r[2], date.fromisoformat(r[3]) if r[3] else None, r[4], bool(r[5])), new

    def notices(self, unseen_only: bool = True, mark_seen: bool = False) -> list[Notice]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, title, body, day, source, seen FROM notices" + (" WHERE seen=0" if unseen_only else "")
                + " ORDER BY day IS NULL, day DESC, id DESC LIMIT 20").fetchall()
            if mark_seen and rows:
                self._db.executemany("UPDATE notices SET seen=1 WHERE id=?", [(r[0],) for r in rows])
                self._db.commit()
        return [Notice(r[0], r[1], r[2], date.fromisoformat(r[3]) if r[3] else None, r[4], bool(r[5]))
                for r in rows]
