"""Memory as a gist index: one short line per fact, found by words, expanded only on request.

Tier 1 is the gist (a line of about 30 tokens), tier 2 a stored summary, tier 3 the relevant passages of the
original. Ranking uses SQLite FTS5 BM25 plus recency, a once-at-write importance and how often a gist was
actually used; nothing here calls a model.
"""
from __future__ import annotations

import math
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

GIST_CHARS = 160
SUMMARY_CHARS = 600
RECENCY_HALF_LIFE_DAYS = 45.0
_WORD = re.compile(r"[a-z0-9]+")
_STOP = set("""a an the to of and or in on at it is are was were be been am do does did me my i you your we our us this
that these those with from by as so if then than can could would should will just please some any about up out
into over for what which who whom when where why how not no yes have has had having get got make want need like let
know tell say said ask""".split())
CATEGORIES = {
    "School": r"school|homework|teacher|class|exam|test|marks|subject|maths|science|english|hindi|notice|fee|"
              r"assignment|syllabus|college|study|tuition",
    "Health": r"doctor|medicine|pills?|health|sick|fever|dentist|gym|diet|sleep|exercise|headache|hospital",
    "Computers": r"code|coding|python|linux|laptop|computer|software|install|bug|server|github|app|program|"
                 r"arch|mint|terminal|api|website",
    "Money": r"money|pay|paid|price|cost|rupees?|bank|budget|buy|bought|order|shopping|amazon|flipkart|salary",
    "Music": r"music|song|songs|spotify|playlist|album|artist|listen|guitar|piano",
    "People": r"friend|mother|father|mom|dad|brother|sister|family|cousin|uncle|aunt|birthday|wedding",
    "Plans": r"plan|tomorrow|next week|trip|travel|holiday|vacation|remind|deadline|schedule|appointment",
    "Home": r"light|lights|fan|door|lock|kitchen|bedroom|room|switch|ac|heater",
}
_CATEGORY_RE = {k: re.compile(r"\b(?:" + v + r")\b", re.I) for k, v in CATEGORIES.items()}
_IMPORTANT = re.compile(r"\b(\d{1,4}|deadline|due|exam|birthday|prefer|always|never|remember|allerg|promise|"
                        r"appointment|password|important|must)\b", re.I)


def words(text: str) -> list[str]:
    return [w for w in _WORD.findall((text or "").lower()) if w not in _STOP and len(w) > 1]


def source_label(kind: str) -> str:
    """Where a remembered line came from, in words you would say."""
    return {"fact": "you told me", "day": "our conversations", "month": "our conversations",
            "job": "a job I ran"}.get(kind, kind)


def categorize(text: str) -> str:
    best, score = "General", 0
    for name, rx in _CATEGORY_RE.items():
        n = len(rx.findall(text))
        if n > score:
            best, score = name, n
    return best


def importance(text: str) -> int:
    return min(9, 4 + len(set(m.group(0).lower() for m in _IMPORTANT.finditer(text))))


@dataclass(frozen=True)
class Gist:
    id: int
    kind: str
    category: str
    title: str
    gist: str
    summary: str
    source_ref: str
    importance: int
    created: float
    private: bool
    score: float = 0.0


class GistIndex:
    def __init__(self, path: Path, clock=time.time):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        self._clock = clock
        with self._lock:
            self._db.executescript(
                "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, kind TEXT, category TEXT, title TEXT,"
                " gist TEXT, summary TEXT, source_ref TEXT, importance INTEGER, created REAL, private INTEGER DEFAULT 0,"
                " uses INTEGER DEFAULT 0, ignored INTEGER DEFAULT 0, dedupe TEXT UNIQUE);"
                "CREATE VIRTUAL TABLE IF NOT EXISTS gist_fts USING fts5(gist, title, category, "
                "content='items', content_rowid='id', tokenize='porter unicode61');")
            self._db.commit()

    # -- writing ----------------------------------------------------------------------------------------
    def add(self, kind: str, gist: str, *, title: str = "", category: str | None = None, summary: str = "",
            source_ref: str = "", importance_: int | None = None, created: float | None = None,
            private: bool = False) -> int | None:
        """The new id, or None when the same gist from the same source is already there."""
        gist = " ".join(str(gist).split())[:GIST_CHARS]
        if len(gist) < 6:
            return None
        category = category or categorize(f"{title} {gist}")
        dedupe = f"{source_ref}|{' '.join(words(gist))}"
        with self._lock:
            try:
                cur = self._db.execute(
                    "INSERT INTO items(kind, category, title, gist, summary, source_ref, importance, created, private,"
                    " dedupe) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (kind, category, title[:80], gist, str(summary)[:SUMMARY_CHARS], source_ref,
                     importance_ if importance_ is not None else importance(gist),
                     created if created is not None else self._clock(), int(private), dedupe))
            except sqlite3.IntegrityError:
                return None
            self._db.execute("INSERT INTO gist_fts(rowid, gist, title, category) VALUES (?,?,?,?)",
                             (cur.lastrowid, gist, title[:80], category))
            self._db.commit()
            return cur.lastrowid

    def forget(self, ident: int) -> bool:
        with self._lock:
            row = self._db.execute("SELECT gist, title, category FROM items WHERE id=?", (ident,)).fetchone()
            if row is None:
                return False
            self._db.execute("INSERT INTO gist_fts(gist_fts, rowid, gist, title, category) VALUES ('delete',?,?,?,?)",
                             (ident, *row))
            self._db.execute("DELETE FROM items WHERE id=?", (ident,))
            self._db.commit()
            return True

    def update(self, ident: int, gist: str) -> bool:
        """Change the words of one gist (fact correction); the search index follows."""
        gist = " ".join(str(gist).split())[:GIST_CHARS]
        if len(gist) < 6:
            return False
        with self._lock:
            row = self._db.execute("SELECT gist, title, category, source_ref FROM items WHERE id=?",
                                   (ident,)).fetchone()
            if row is None:
                return False
            old_gist, title, category, source_ref = row
            self._db.execute("INSERT INTO gist_fts(gist_fts, rowid, gist, title, category) VALUES ('delete',?,?,?,?)",
                             (ident, old_gist, title, category))
            self._db.execute("UPDATE items SET gist=?, dedupe=? WHERE id=?",
                             (gist, f"{source_ref}|{' '.join(words(gist))}|{ident}", ident))
            self._db.execute("INSERT INTO gist_fts(rowid, gist, title, category) VALUES (?,?,?,?)",
                             (ident, gist, title, category))
            self._db.commit()
            return True

    def mark(self, ids: list[int], used: set[int]) -> None:
        with self._lock:
            for i in ids:
                col = "uses" if i in used else "ignored"
                self._db.execute(f"UPDATE items SET {col}={col}+1 WHERE id=?", (i,))
            self._db.commit()

    # -- reading ----------------------------------------------------------------------------------------
    @staticmethod
    def _row(r, score: float = 0.0) -> Gist:
        return Gist(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], bool(r[9]), score)

    _COLS = "id, kind, category, title, gist, summary, source_ref, importance, created, private"

    def get(self, ident: int) -> Gist | None:
        with self._lock:
            r = self._db.execute(f"SELECT {self._COLS} FROM items WHERE id=?", (ident,)).fetchone()
        return self._row(r) if r else None

    def by_source(self, source_ref: str, limit: int = 4) -> list[Gist]:
        with self._lock:
            rows = self._db.execute(f"SELECT {self._COLS} FROM items WHERE source_ref=? ORDER BY importance DESC "
                                    "LIMIT ?", (source_ref, limit)).fetchall()
        return [self._row(r) for r in rows]

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM items").fetchone()[0]

    def map(self, include_private: bool = False) -> str:
        with self._lock:
            rows = self._db.execute(
                "SELECT category, COUNT(*) FROM items" + ("" if include_private else " WHERE private=0")
                + " GROUP BY category ORDER BY COUNT(*) DESC LIMIT 8").fetchall()
        return " · ".join(f"{c} {n}" for c, n in rows)

    def search(self, query: str, limit: int = 8, include_private: bool = False,
               now: float | None = None) -> list[Gist]:
        terms = []
        for w in words(query)[:12]:
            if w not in terms:
                terms.append(w)
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)  # quoted: user words never become FTS syntax
        now = now if now is not None else self._clock()
        sql = (f"SELECT {', '.join('i.' + c.strip() for c in self._COLS.split(','))}, i.uses, i.ignored, "
               "bm25(gist_fts, 1.0, 2.0, 0.5) AS rank FROM gist_fts JOIN items i ON i.id = gist_fts.rowid "
               "WHERE gist_fts MATCH ?" + ("" if include_private else " AND i.private = 0") + " LIMIT 60")
        with self._lock:
            try:
                rows = self._db.execute(sql, (match,)).fetchall()
            except sqlite3.OperationalError:
                return []
        ranked = []
        for r in rows:
            *base, uses, ignored, rank = r
            relevance = -rank  # bm25 is lower-is-better and negative
            age_days = max(0.0, (now - base[8]) / 86400)
            recency = 0.5 + 0.5 * math.pow(0.5, age_days / RECENCY_HALF_LIFE_DAYS)
            usefulness = (uses + 1) / (uses + ignored + 2) * 2  # 1.0 when untested
            ranked.append(self._row(base, relevance * (0.6 + base[7] / 10) * recency * (0.5 + usefulness / 2)))
        ranked.sort(key=lambda g: -g.score)
        return ranked[:limit]

    def within_budget(self, found: list[Gist], max_tokens: int) -> list[Gist]:
        """Keep gists, best first, while the lines fit the token budget (about 4 characters a token)."""
        out, used = [], 0
        for g in found:
            cost = (len(g.gist) + 24) // 4
            if used + cost > max_tokens and out:
                break
            out.append(g)
            used += cost
        return out


# ---- turning summaries into gists -------------------------------------------------------------------------
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def split_claims(text: str, max_len: int = GIST_CHARS) -> list[str]:
    """Atomic claims from a summary: one per bullet or sentence, long ones cut at a word, tiny ones joined."""
    pieces: list[str] = []
    for line in (text or "").splitlines():
        line = line.strip().lstrip("-*•0123456789.) ").strip()
        if not line:
            continue
        pieces.extend(s.strip() for s in _SENTENCE.split(line) if s.strip())
    claims: list[str] = []
    buf = ""
    for p in pieces:
        if len(p) > max_len:
            p = p[: max_len - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"
        if buf and len(buf) + len(p) + 1 <= max_len and len(buf) < 15:
            buf = f"{buf} {p}"
            continue
        if buf:
            claims.append(buf)
        buf = p
    if buf:
        claims.append(buf)
    return [c for c in claims if len(c) >= 8][:12]


def add_summary(index: GistIndex, kind: str, label: str, summary: str, *, created: float, private: bool,
                source_ref: str) -> int:
    """One gist per claim of a day or month summary. Returns how many were new."""
    added = 0
    for claim in split_claims(summary):
        if index.add(kind, claim, title=f"{kind} {label}", summary=summary, source_ref=source_ref,
                     created=created, private=private) is not None:
            added += 1
    return added


def backfill(index: GistIndex, archive) -> int:
    """One-off import of the day and month summaries that already exist (idempotent: gists dedupe)."""
    from datetime import datetime

    total = 0
    root = Path(archive.root)
    for folder, kind in (("months", "month"), ("days", "day"), ("days/.merged", "day")):
        for path in sorted((root / folder).glob("*.md" if kind == "month" or folder == "days" else "*")):
            label = path.stem
            if not re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", label):
                continue
            try:
                text = path.read_text(encoding="utf-8")
                stamp = datetime.fromisoformat(label if len(label) == 10 else label + "-28").timestamp()
            except (OSError, ValueError):
                continue
            private = kind == "day" and any(e.get("private") for e in archive._entries(label))
            total += add_summary(index, kind, label, text, created=stamp, private=private,
                                 source_ref=f"{'months' if kind == 'month' else 'days'}/{label}")
    return total
