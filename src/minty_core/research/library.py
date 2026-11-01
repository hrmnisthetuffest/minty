"""The library: every research is a notebook, filed by category, searchable by claim, answered from later."""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from minty_core.fsutil import atomic_write_text
from minty_core.gists import words as content_words

CATEGORIES = {
    "Health": r"health|disease|symptom|medic|drug|vaccine|cancer|diabetes|therapy|clinical|nutrition|sleep|mental|"
              r"heart|virus|infection|vitamin|exercise|diet",
    "Computers": r"computer|software|linux|python|code|programming|network|security|cpu|gpu|ai\b|machine learning|"
                 r"algorithm|database|browser|internet|app|operating system",
    "Science": r"physics|chemistry|biology|climate|quantum|space|astronomy|genetic|ocean|cell|energy|battery|"
               r"theory|experiment|evolution|geology|water cycle|ecosystem",
    "Finance": r"finance|money|invest|stock|market|bank|tax|loan|inflation|economy|crypto|budget|insurance",
    "School": r"school|exam|syllabus|homework|curriculum|college|university|study|education|learning|teacher",
    "News": r"news|election|government|war|policy|minister|court|law|protest|summit",
    "Home": r"home|house|kitchen|repair|appliance|garden|cooking|recipe|furniture|cleaning",
    "Projects": r"project|build|design|plan|prototype|architecture|roadmap",
}
_CAT = {k: re.compile(v, re.I) for k, v in CATEGORIES.items()}
FRESH_DAYS = {"news": 14, "web": 180, "reference": 365, "academic": 5 * 365}
_SLUG = re.compile(r"[^a-z0-9]+")


def categorise(text: str) -> str:
    best, score = "General", 0
    for name, rx in _CAT.items():
        n = len(rx.findall(text))
        if n > score:
            best, score = name, n
    return best


def slugify(text: str) -> str:
    return _SLUG.sub("-", text.lower()).strip("-")[:48] or "research"


@dataclass
class NoteRef:
    category: str
    slug: str
    path: Path


class Library:
    def __init__(self, root: Path, index_path: Path | None = None, clock=time.time):
        self.root = Path(root)
        self._clock = clock
        self._lock = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(index_path or self.root / "library.db"), check_same_thread=False)
        with self._lock:
            self._db.executescript(
                "CREATE TABLE IF NOT EXISTS claims (id INTEGER PRIMARY KEY, note TEXT, source TEXT, domain TEXT,"
                " kind TEXT, claim TEXT, quote TEXT, support INTEGER, disputed INTEGER DEFAULT 0);"
                "CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts USING fts5(claim, content='claims', content_rowid='id',"
                " tokenize='porter unicode61');")
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- writing ----------------------------------------------------------------------------------------
    def save(self, topic: str, depth: str, note_md: str, sources: list[dict], claims: list[dict],
             summary: str, confidence: str, category: str | None = None) -> NoteRef:
        category = category or categorise(f"{topic} {summary}")
        base = slugify(topic)
        folder = self.root / category / base
        n = 2
        while folder.exists():
            folder = self.root / category / f"{base}-{n}"
            n += 1
        folder.mkdir(parents=True)
        slug = folder.name
        atomic_write_text(folder / "note.md", note_md)
        atomic_write_text(folder / "sources.jsonl", "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in sources))
        atomic_write_text(folder / "claims.jsonl", "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in claims))
        atomic_write_text(folder / "meta.json", json.dumps({
            "topic": topic, "depth": depth, "category": category, "slug": slug, "created": self._clock(),
            "summary": summary, "confidence": confidence, "sources": len(sources), "claims": len(claims)},
            ensure_ascii=False, indent=1))
        domains = {s["id"]: (s.get("domain", ""), s.get("kind", "web")) for s in sources}
        with self._lock:
            for c in claims:
                dom, kind = domains.get(c["source"], ("", "web"))
                cur = self._db.execute(
                    "INSERT INTO claims(note, source, domain, kind, claim, quote, support) VALUES (?,?,?,?,?,?,?)",
                    (f"{category}/{slug}", c["source"], dom, kind, c["text"], c["quote"], c.get("support", 1)))
                self._db.execute("INSERT INTO claims_fts(rowid, claim) VALUES (?,?)", (cur.lastrowid, c["text"]))
            self._db.commit()
        return NoteRef(category, slug, folder)

    def attach(self, slug: str, filename: str, text: str) -> Path | None:
        """Put another file (a blueprint) into a note's folder."""
        folder = self._find(slug)
        name = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:60]
        if folder is None or not name:
            return None
        atomic_write_text(folder / name, text)
        return folder / name

    def move(self, slug: str, category: str) -> str:
        category = category.strip().title()
        if not re.fullmatch(r"[A-Za-z ]{2,24}", category):
            return "Error: give a short category name."
        src = self._find(slug)
        if src is None:
            return f"Error: no research called {slug!r}."
        dest = self.root / category / src.name
        if dest.exists():
            return "Error: a note with that name is already there."
        dest.parent.mkdir(exist_ok=True)
        shutil.move(str(src), str(dest))
        meta = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
        meta["category"] = category
        atomic_write_text(dest / "meta.json", json.dumps(meta, ensure_ascii=False, indent=1))
        with self._lock:
            self._db.execute("UPDATE claims SET note=? WHERE note=?", (f"{category}/{src.name}",
                                                                      f"{src.parent.name}/{src.name}"))
            self._db.commit()
        return f"Moved {src.name} to {category}."

    def dispute(self, claim_id: int) -> str:
        """Mark a claim wrong; its domain's weight is lowered for later source selection."""
        with self._lock:
            row = self._db.execute("SELECT domain FROM claims WHERE id=?", (claim_id,)).fetchone()
            if row is None:
                return ""
            self._db.execute("UPDATE claims SET disputed=1 WHERE id=?", (claim_id,))
            self._db.commit()
        return row[0]

    def weights(self) -> dict[str, float]:
        with self._lock:
            rows = self._db.execute("SELECT domain, COUNT(*) FROM claims WHERE disputed=1 GROUP BY domain").fetchall()
        return {d: max(0.3, 0.7 ** n) for d, n in rows if d}

    # -- reading ----------------------------------------------------------------------------------------
    def _find(self, slug: str) -> Path | None:
        slug = slugify(slug)
        for meta in self.root.glob("*/*/meta.json"):
            if meta.parent.name == slug:
                return meta.parent
        return None

    def meta(self, slug: str) -> dict | None:
        folder = self._find(slug)
        try:
            return json.loads((folder / "meta.json").read_text(encoding="utf-8")) if folder else None
        except (OSError, ValueError):
            return None

    def note(self, slug: str) -> str:
        folder = self._find(slug)
        return (folder / "note.md").read_text(encoding="utf-8") if folder else ""

    def list(self, category: str | None = None) -> list[dict]:
        out = []
        for meta in sorted(self.root.glob("*/*/meta.json")):
            try:
                m = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if category is None or m.get("category", "").lower() == category.lower():
                out.append(m)
        return sorted(out, key=lambda m: -m.get("created", 0))

    def ask(self, question: str, limit: int = 5) -> tuple[list[dict], dict[str, dict]]:
        """Stored claims that answer a question, best first, with their notes' metadata."""
        terms = [w for w in dict.fromkeys(content_words(question))][:10]
        if not terms:
            return [], {}
        match = " OR ".join(f'"{t}"' for t in terms)
        with self._lock:
            try:
                rows = self._db.execute(
                    "SELECT c.id, c.note, c.source, c.domain, c.kind, c.claim, c.quote, c.support, c.disputed, "
                    "bm25(claims_fts) FROM claims_fts JOIN claims c ON c.id = claims_fts.rowid "
                    "WHERE claims_fts MATCH ? AND c.disputed = 0 LIMIT 40", (match,)).fetchall()
            except sqlite3.OperationalError:
                return [], {}
        scored = sorted(rows, key=lambda r: r[9] - 0.4 * r[7])  # bm25: lower is better; support helps
        hits, metas = [], {}
        for r in scored[:limit]:
            hits.append({"id": r[0], "note": r[1], "source": r[2], "domain": r[3], "kind": r[4], "claim": r[5],
                         "quote": r[6], "support": r[7]})
            metas.setdefault(r[1], self.meta(r[1].split("/", 1)[1]) or {})
        return hits, metas

    def freshness(self, meta: dict, kinds: set[str]) -> str:
        """'' when current, else a sentence about how old it is."""
        age = (self._clock() - float(meta.get("created") or 0)) / 86400
        limit = min((FRESH_DAYS.get(k, 180) for k in kinds), default=180)
        if age <= limit:
            return ""
        when = datetime.fromtimestamp(meta.get("created", 0)).strftime("%B %Y")
        return f"This is from {when}, so it may be out of date; want me to refresh it?"
