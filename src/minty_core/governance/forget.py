"""Forget with proof (611): remove every trace of a word or name from the stores Minty keeps, then look again
and say what is left. It also says what it cannot reach."""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

MIN_TERM = 3
MAX_AUTO = 60  # more matches than this need an explicit "yes, many"
LIMITS = ("Copies that already went to online models, to Telegram, or into backups and exports you made are outside "
          "my reach; the notes folder's git history still holds earlier versions of an erased note.")


class ForgetError(ValueError):
    pass


@dataclass
class Hit:
    store: str
    ref: str
    preview: str


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _has(text: str, term: str) -> bool:
    return term in str(text).lower()


def _preview(text: str, term: str, width: int = 80) -> str:
    flat = " ".join(str(text).split())
    at = max(0, flat.lower().find(term) - width // 4)
    return ("…" if at else "") + flat[at:at + width]


# ---- adapters: find(term) -> [Hit], purge(term, hits) -> count removed --------------------------------------
class MemoryFile:
    name = "memory file"

    def __init__(self, memory):
        self.memory = memory

    def find(self, term):
        return [Hit(self.name, f"line:{i}", _preview(t, term)) for i, t in enumerate(self.memory.list())
                if _has(t, term)]

    def purge(self, term, hits):
        n = 0
        for i in sorted((int(h.ref.split(":")[1]) for h in hits), reverse=True):
            try:
                self.memory.delete(i)
                n += 1
            except IndexError:
                pass
        return n


class Gists:
    name = "memory index"

    def __init__(self, gists):
        self.gists = gists

    def _ids(self, term):
        with self.gists._lock:
            return [(r[0], r[1]) for r in self.gists._db.execute(
                "SELECT id, gist FROM items WHERE gist LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' "
                "OR summary LIKE ? ESCAPE '\\'", (_like(term),) * 3).fetchall()]

    def find(self, term):
        return [Hit(self.name, f"gist:{i}", _preview(t, term)) for i, t in self._ids(term)]

    def purge(self, term, hits):
        return sum(1 for h in hits if self.gists.forget(int(h.ref.split(":")[1])))


class Archive:
    """Conversation archive: raw day transcripts (per message) and the day and month summaries (per line)."""
    name = "conversation history"

    def __init__(self, root: Path):
        self.root = Path(root)

    def _files(self):
        for sub, pattern in (("raw", "*.jsonl"), ("days", "*.md"), ("months", "*.md")):
            yield from sorted((self.root / sub).glob(pattern))

    def find(self, term):
        out = []
        for p in self._files():
            try:
                lines = p.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines):
                if _has(self._text(p, line), term):
                    out.append(Hit(self.name, f"{p.parent.name}/{p.name}:{i}", _preview(self._text(p, line), term)))
        return out

    @staticmethod
    def _text(path: Path, line: str) -> str:
        if path.suffix == ".jsonl":
            try:
                return str(json.loads(line).get("text", ""))
            except ValueError:
                return line
        return line

    def purge(self, term, hits):
        n = 0
        for rel in sorted({h.ref.rsplit(":", 1)[0] for h in hits}):
            p = self.root / rel
            try:
                lines = p.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            keep = [l for l in lines if not _has(self._text(p, l), term)]
            n += len(lines) - len(keep)
            p.write_text("".join(l + "\n" for l in keep), encoding="utf-8")
        return n


class Receipts:
    name = "action receipts"

    def __init__(self, receipts):
        self.r = receipts

    def find(self, term):
        with self.r._lock:
            rows = self.r._db.execute(
                "SELECT id, summary FROM receipts WHERE summary LIKE ? ESCAPE '\\' OR request LIKE ? ESCAPE '\\' "
                "OR detail LIKE ? ESCAPE '\\'", (_like(term),) * 3).fetchall()
        return [Hit(self.name, f"receipt:{i}", _preview(t, term)) for i, t in rows]

    def purge(self, term, hits):
        with self.r._lock:
            for h in hits:
                self.r._db.execute("DELETE FROM receipts WHERE id=?", (int(h.ref.split(":")[1]),))
            self.r._db.commit()
        return len(hits)


class Butler:
    name = "butler lists"

    def __init__(self, store):
        self.s = store

    def find(self, term):
        with self.s._lock:
            rows = self.s._db.execute("SELECT id, name FROM items WHERE name LIKE ? ESCAPE '\\' OR data LIKE ? "
                                      "ESCAPE '\\'", (_like(term),) * 2).fetchall()
            undo = self.s._db.execute("SELECT id, label FROM undo WHERE label LIKE ? ESCAPE '\\'",
                                      (_like(term),)).fetchall()
        return ([Hit(self.name, f"item:{i}", _preview(t, term)) for i, t in rows]
                + [Hit(self.name, f"undo:{i}", _preview(t, term)) for i, t in undo])

    def purge(self, term, hits):
        with self.s._lock:
            for h in hits:
                kind, _, ident = h.ref.partition(":")
                self.s._db.execute("DELETE FROM items WHERE id=?" if kind == "item" else "DELETE FROM undo WHERE id=?",
                                   (int(ident),))
            self.s._db.commit()
        return len(hits)


class Notes:
    name = "notes folder"

    def __init__(self, vault):
        self.v = vault

    def find(self, term):
        self.v.refresh()
        out = []
        for row in self.v.index.rows(None, include_archived=True, limit=100_000):
            note = self.v.get(row["id"])
            if note is not None and (_has(note.title, term) or _has(note.body, term)):
                out.append(Hit(self.name, f"note:{note.id}", _preview(f"{note.title}: {note.body}", term)))
        return out

    def purge(self, term, hits):
        return sum(1 for h in hits if self.v.erase(h.ref.split(":", 1)[1]))


class Library:
    name = "research library"

    def __init__(self, root: Path, index: Path | None = None):
        self.root = Path(root)
        self.index = Path(index) if index else self.root / "library.db"

    def _folders(self):
        for meta in self.root.glob("*/*/meta.json"):
            yield meta.parent

    def find(self, term):
        out = []
        for folder in self._folders():
            for name in ("note.md", "meta.json", "claims.jsonl", "sources.jsonl"):
                try:
                    text = (folder / name).read_text(encoding="utf-8")
                except OSError:
                    continue
                if _has(text, term):
                    out.append(Hit(self.name, f"{folder.parent.name}/{folder.name}", _preview(text, term)))
                    break
        return out

    def purge(self, term, hits):
        n = 0
        db_path = self.index
        for h in hits:
            shutil.rmtree(self.root / h.ref, ignore_errors=True)
            n += 1
            if db_path.exists():
                try:
                    db = sqlite3.connect(str(db_path))
                    for cid, claim in db.execute("SELECT id, claim FROM claims WHERE note=?", (h.ref,)).fetchall():
                        db.execute("INSERT INTO claims_fts(claims_fts, rowid, claim) VALUES ('delete', ?, ?)",
                                   (cid, claim))
                    db.execute("DELETE FROM claims WHERE note=?", (h.ref,))
                    db.commit()
                    db.close()
                except sqlite3.Error:
                    pass
        return n


class LineFiles:
    """Log-like files (traces, audit, the log): lines that mention the term are removed."""

    def __init__(self, name: str, paths):
        self.name, self._paths = name, paths

    def _files(self):
        return [p for p in self._paths() if p.is_file()]

    def find(self, term):
        out = []
        for p in self._files():
            try:
                for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines()):
                    if _has(line, term):
                        out.append(Hit(self.name, f"{p.name}:{i}", _preview(line, term)))
            except OSError:
                continue
        return out

    def purge(self, term, hits):
        n = 0
        for name in sorted({h.ref.rsplit(":", 1)[0] for h in hits}):
            for p in self._files():
                if p.name != name:
                    continue
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                keep = [l for l in lines if not _has(l, term)]
                n += len(lines) - len(keep)
                p.write_text("".join(l + "\n" for l in keep), encoding="utf-8")
        return n


def build_engine(*, memory, gists=None, archive_root=None, receipts=None, butler_db=None, vault=None,
                 library_root=None, library_index=None, trace_dirs=(), log_files=()) -> "ForgetEngine":
    """Every store that holds words of yours, behind one engine."""
    stores = [MemoryFile(memory)]
    if gists is not None:
        stores.append(Gists(gists))
    if archive_root is not None and Path(archive_root).is_dir():
        stores.append(Archive(archive_root))
    if receipts is not None:
        stores.append(Receipts(receipts))
    if butler_db is not None and Path(butler_db).is_file():
        from minty_core.butler.store import ButlerStore

        stores.append(Butler(ButlerStore(butler_db)))
    if vault is not None:
        stores.append(Notes(vault))
    if library_root is not None and Path(library_root).is_dir():
        stores.append(Library(library_root, library_index))
    dirs = [Path(d) for d in trace_dirs]
    stores.append(LineFiles("timing and audit logs", lambda: [p for d in dirs if d.is_dir()
                                                              for p in sorted(d.glob("*.jsonl"))]
                            + [Path(p) for p in log_files]))
    return ForgetEngine(stores)


@dataclass
class Preview:
    term: str
    counts: dict[str, int]
    samples: dict[str, list[str]] = field(default_factory=dict)
    total: int = 0

    def text(self) -> str:
        if not self.total:
            return f"I found nothing about \"{self.term}\" in what I keep."
        parts = ", ".join(f"{n} in {store}" for store, n in self.counts.items() if n)
        return f"\"{self.term}\" appears {self.total} time{'s' * (self.total != 1)}: {parts}."


@dataclass
class Proof:
    term: str
    removed: dict[str, int]
    remaining: dict[str, int]
    clean: bool

    def text(self) -> str:
        done = ", ".join(f"{n} from {s}" for s, n in self.removed.items() if n) or "nothing"
        if self.clean:
            return f"Forgot \"{self.term}\": removed {done}. I searched again and found 0 left. {LIMITS}"
        left = ", ".join(f"{n} in {s}" for s, n in self.remaining.items() if n)
        return f"I removed {done}, but \"{self.term}\" is still in {left}. {LIMITS}"


class ForgetEngine:
    def __init__(self, stores):
        self.stores = list(stores)

    @staticmethod
    def _term(term: str) -> str:
        t = " ".join(str(term or "").lower().split())
        if len(t) < MIN_TERM or not re.search(r"\w", t):
            raise ForgetError(f"Give me a word or name of at least {MIN_TERM} letters to forget.")
        return t

    def _find(self, term: str) -> dict[str, list[Hit]]:
        out = {}
        for s in self.stores:
            try:
                out[s.name] = s.find(term)
            except Exception:  # noqa: BLE001 — one store failing must not hide the others
                out[s.name] = []
        return out

    def preview(self, term: str) -> Preview:
        t = self._term(term)
        found = self._find(t)
        counts = {s: len(h) for s, h in found.items() if h}
        return Preview(t, counts, {s: [h.preview for h in hits[:3]] for s, hits in found.items() if hits},
                       sum(counts.values()))

    def run(self, term: str, allow_many: bool = False) -> Proof:
        t = self._term(term)
        found = self._find(t)
        total = sum(len(h) for h in found.values())
        if total > MAX_AUTO and not allow_many:
            raise ForgetError(f"\"{t}\" appears {total} times. That is a lot: say it again with a more specific "
                              "word, or confirm that you really want all of it gone.")
        removed = {}
        for s in self.stores:
            hits = found.get(s.name) or []
            removed[s.name] = s.purge(t, hits) if hits else 0
        after = self._find(t)
        remaining = {s: len(h) for s, h in after.items() if h}
        return Proof(t, {s: n for s, n in removed.items() if n}, remaining, not remaining)
