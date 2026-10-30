"""The Second Brain's store: what Minty knows that is not already a person, an organisation, a project or a decision.

Four kinds of record, each with one job:

* **sources**: the originals (a typed thought, a page, a PDF, a transcript). Written once, never edited; a file original is kept content-addressed with mode 0600.
* **items**: the knowledge itself (an idea, a finding, a lesson, a question, a hypothesis ...), each saying who wrote it (`authored`: you / minty / outside), how far it is
  checked (`status`) and which sources it rests on (`evidence`). A summary lives beside the body, marked generated; it never replaces the original.
* **links**: typed, sourced relationships between any two things, by *reference* (`item:`, `source:`, `world:`, `vault:`, `decision:`, `mission:`, `flow:`, `playbook:`), so one edge
  layer spans every store and nothing is copied. A link can be confirmed, merely suggested, or rejected.
* **captures**: the inbox. Raw input waits here, is processed into items and suggested links, and can be inspected, edited, reprocessed, archived or (by the owner) deleted.

People, organisations, projects and decisions keep their home in the world model; they are *referenced* from here, not duplicated. Content from outside can only become an `outside`,
`unverified` item with `suggested` links: a turn that read a page cannot make anything "yours", "verified" or "confirmed". Search is SQLite FTS5 plus the character n-gram similarity
the notes folder already uses (`vault.gate.HashEmbedder`): no vector database, no download, nothing to keep in sync.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, Iterable

from minty_core.vault.index import words

KINDS = ("note", "idea", "concept", "finding", "article", "document", "observation", "lesson", "question", "hypothesis", "opportunity", "reference", "insight", "dump")
ROUTED = {"person", "organization", "project", "product", "place", "tool", "event", "company", "team"}  # live in the world model; referenced from here
AUTHORED = ("you", "minty", "outside")
STATUS = ("unverified", "verified", "disputed", "outdated")
LINK_STATUS = ("confirmed", "suggested", "rejected")
CAPTURE_STATUS = ("pending", "processing", "review", "filed", "failed", "archived")
SOURCE_KINDS = ("text", "link", "file", "pdf", "image", "voice", "chat", "task", "mail")
RELS = ("relates to", "part of", "supports", "contradicts", "answers", "derived from", "about", "uses", "duplicate of", "led to", "inspired by", "mentions")
REF_SYSTEMS = ("item", "source", "world", "vault", "decision", "mission", "flow", "playbook")
LIMITS = {"title": 140, "body": 60_000, "summary": 1_200, "why": 280, "tag": 32, "project": 60, "raw": 200_000, "quote": 400, "name": 120}
STALE_DAYS = 120
_REF = re.compile(r"^(" + "|".join(REF_SYSTEMS) + r"):(.{1,160})$", re.S)
_SPACE = re.compile(r"[ \t\r\f\v]+")


class KnowledgeError(ValueError):
    """A request the store refuses; the message is safe to say aloud."""


def clean(text, limit: int, what: str = "text", *, keep_lines: bool = False) -> str:
    text = str(text or "").replace("\x00", "")
    if keep_lines:
        text = "\n".join(_SPACE.sub(" ", line).rstrip() for line in text.splitlines()).strip()
    else:
        text = " ".join(text.split())
    if len(text) > limit:
        raise KnowledgeError(f"{what} is too long ({len(text)} characters; the limit is {limit}).")
    return text


def parse_ref(ref: str) -> tuple[str, str]:
    m = _REF.match(str(ref or "").strip())
    if not m:
        raise KnowledgeError(f"'{str(ref)[:40]}' is not a reference (use item:ID, source:ID, world:NAME, vault:ID, decision:ID, mission:ID, flow:ID or playbook:NAME).")
    return m.group(1), m.group(2).strip()


def _tags(tags: Iterable[str]) -> list[str]:
    out: list[str] = []
    for t in tags or ():
        t = re.sub(r"[^\w\- ]", "", str(t).lower()).strip().replace(" ", "-")[: LIMITS["tag"]]
        if t and t not in out:
            out.append(t)
    return out[:12]


class KnowledgeStore:
    def __init__(self, path: Path, originals: Path | None = None, clock: Callable[[], float] = time.time):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.close(os.open(path, os.O_CREAT | os.O_RDWR, 0o600))
        self.originals = Path(originals) if originals else path.parent / "knowledge-originals"
        self._clock, self._lock = clock, threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, kind TEXT NOT NULL, uri TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', captured REAL NOT NULL,"
            " sha256 TEXT NOT NULL DEFAULT '', path TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL DEFAULT 'you', channel TEXT NOT NULL DEFAULT '',"
            " meta TEXT NOT NULL DEFAULT '{}', private INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'kept');"
            "CREATE INDEX IF NOT EXISTS sources_sha ON sources(sha256);"
            "CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',"
            " authored TEXT NOT NULL DEFAULT 'you', status TEXT NOT NULL DEFAULT 'unverified', created REAL NOT NULL, updated REAL NOT NULL, reviewed REAL NOT NULL DEFAULT 0,"
            " project TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]', private INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0, attrs TEXT NOT NULL DEFAULT '{}');"
            "CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(title, body, summary, tags, tokenize='porter unicode61');"
            "CREATE VIRTUAL TABLE IF NOT EXISTS sources_fts USING fts5(title, body, tokenize='porter unicode61');"
            "CREATE TABLE IF NOT EXISTS evidence (item TEXT NOT NULL, source TEXT NOT NULL, quote TEXT NOT NULL DEFAULT '', created REAL NOT NULL, PRIMARY KEY (item, source, quote));"
            "CREATE TABLE IF NOT EXISTS links (id INTEGER PRIMARY KEY AUTOINCREMENT, src TEXT NOT NULL, rel TEXT NOT NULL, dst TEXT NOT NULL, why TEXT NOT NULL DEFAULT '',"
            " status TEXT NOT NULL DEFAULT 'confirmed', by TEXT NOT NULL DEFAULT 'you', score REAL NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL, UNIQUE (src, rel, dst));"
            "CREATE INDEX IF NOT EXISTS links_dst ON links(dst);"
            "CREATE TABLE IF NOT EXISTS captures (id TEXT PRIMARY KEY, created REAL NOT NULL, updated REAL NOT NULL, channel TEXT NOT NULL DEFAULT '', kind_hint TEXT NOT NULL DEFAULT '',"
            " origin TEXT NOT NULL DEFAULT 'you', raw TEXT NOT NULL DEFAULT '', source_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', result TEXT NOT NULL DEFAULT '{}',"
            " error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0, private INTEGER NOT NULL DEFAULT 0, meta TEXT NOT NULL DEFAULT '{}');"
            "CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, ref TEXT NOT NULL, ts REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}');"
            "CREATE INDEX IF NOT EXISTS history_ref ON history(ref);")
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- helpers ---------------------------------------------------------------------------------------------------
    def _id(self, prefix: str, table: str) -> str:
        while True:
            ident = f"{prefix}-{secrets.token_hex(4)}"
            if not self._db.execute(f"SELECT 1 FROM {table} WHERE id = ?", (ident,)).fetchone():  # noqa: S608 — the table name is ours
                return ident

    def _log(self, ref: str, actor: str, action: str, detail: dict | None = None) -> None:
        self._db.execute("INSERT INTO history (ref, ts, actor, action, detail) VALUES (?,?,?,?,?)", (ref, self._clock(), actor, action, json.dumps(detail or {}, ensure_ascii=False)[:4000]))

    def note_event(self, ref: str, actor: str, action: str, detail: dict | None = None) -> None:
        with self._lock:
            self._log(ref, actor, action, detail)
            self._db.commit()

    def has_event(self, ref: str, action: str) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM history WHERE ref = ? AND action = ? LIMIT 1", (ref, action)).fetchone() is not None

    def history(self, ref: str, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT ts, actor, action, detail FROM history WHERE ref = ? ORDER BY id DESC LIMIT ?", (ref, limit)).fetchall()
        return [{"ts": ts, "actor": a, "action": act, "detail": json.loads(d or "{}")} for ts, a, act, d in rows]

    # ---- sources: the originals ------------------------------------------------------------------------------------
    def add_source(self, kind: str, text: str = "", *, title: str = "", uri: str = "", origin: str = "you", channel: str = "", data: bytes | None = None, name: str = "",
                   meta: dict | None = None, private: bool = False) -> dict:
        """Keep an original. The same bytes or text twice is the same source (`duplicate` is True and nothing is written again)."""
        kind = kind if kind in SOURCE_KINDS else "text"
        text = clean(text, LIMITS["raw"], "the original", keep_lines=True)
        digest = hashlib.sha256(data if data is not None else text.encode("utf-8")).hexdigest()
        with self._lock:
            row = self._db.execute("SELECT id FROM sources WHERE sha256 = ? AND state = 'kept' AND private = ?", (digest, 1 if private else 0)).fetchone()
            if row:
                return {**self.source(row[0]), "duplicate": True}
            sid, path = self._id("s", "sources"), ""
            if data is not None:
                folder = self.originals / digest[:2]
                folder.mkdir(parents=True, exist_ok=True)
                os.chmod(self.originals, 0o700)
                target = folder / digest
                if not target.exists():
                    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as f:
                        f.write(data)
                path = str(target)
            title = clean((title or name or uri or text[:80])[: LIMITS["title"]], LIMITS["title"], "the title")
            now = self._clock()
            self._db.execute("INSERT INTO sources (id, kind, uri, title, captured, sha256, path, text, origin, channel, meta, private) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                             (sid, kind, uri[:500], title, now, digest, path, text, origin if origin in AUTHORED else "outside", channel[:40], json.dumps(meta or {}, ensure_ascii=False), 1 if private else 0))
            cur = self._db.execute("SELECT rowid FROM sources WHERE id = ?", (sid,)).fetchone()
            self._db.execute("INSERT INTO sources_fts(rowid, title, body) VALUES (?,?,?)", (cur[0], title, text))
            self._log(f"source:{sid}", origin, "kept", {"kind": kind, "sha256": digest[:12], "uri": uri[:200]})
            self._db.commit()
        return {**self.source(sid), "duplicate": False}

    def source(self, sid: str) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT id, kind, uri, title, captured, sha256, path, text, origin, channel, meta, private, state FROM sources WHERE id = ?", (sid,)).fetchone()
        if not r:
            return None
        keys = ("id", "kind", "uri", "title", "captured", "sha256", "path", "text", "origin", "channel", "meta", "private", "state")
        d = dict(zip(keys, r))
        d["meta"], d["private"] = json.loads(d["meta"] or "{}"), bool(d["private"])
        return d

    def fill_source_text(self, sid: str, text: str) -> bool:
        """The one exception to "written once": the text read out of a file original, filled in when it was empty (so a retry after a failed read can complete it)."""
        text = clean(text, LIMITS["raw"], "the text", keep_lines=True)
        with self._lock:
            s = self.source(sid)
            if s is None or s["text"].strip() or not text:
                return False
            self._db.execute("UPDATE sources SET text = ? WHERE id = ?", (text, sid))
            row = self._db.execute("SELECT rowid FROM sources WHERE id = ?", (sid,)).fetchone()
            self._db.execute("DELETE FROM sources_fts WHERE rowid = ?", (row[0],))
            self._db.execute("INSERT INTO sources_fts(rowid, title, body) VALUES (?,?,?)", (row[0], s["title"], text))
            self._log(f"source:{sid}", "capture", "text read", {"chars": len(text)})
            self._db.commit()
        return True

    def sources(self, include_private: bool = False, limit: int = 500) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT id FROM sources WHERE state = 'kept'" + ("" if include_private else " AND private = 0") + " ORDER BY captured DESC LIMIT ?", (limit,)).fetchall()
        return [self.source(r[0]) for r in rows]

    # ---- items: the knowledge ---------------------------------------------------------------------------------------
    @staticmethod
    def _item(r) -> dict:
        keys = ("id", "kind", "title", "body", "summary", "authored", "status", "created", "updated", "reviewed", "project", "tags", "private", "archived", "attrs")
        d = dict(zip(keys, r))
        d["tags"], d["attrs"] = json.loads(d["tags"] or "[]"), json.loads(d["attrs"] or "{}")
        d["private"], d["archived"] = bool(d["private"]), bool(d["archived"])
        return d

    _COLS = "id, kind, title, body, summary, authored, status, created, updated, reviewed, project, tags, private, archived, attrs"

    def item(self, ident: str) -> dict | None:
        with self._lock:
            r = self._db.execute(f"SELECT {self._COLS} FROM items WHERE id = ?", (ident,)).fetchone()  # noqa: S608
        return self._item(r) if r else None

    def items(self, *, include_private: bool = False, include_archived: bool = False, kind: str = "", limit: int = 5000) -> list[dict]:
        sql, args = f"SELECT {self._COLS} FROM items WHERE 1=1", []  # noqa: S608
        if not include_private:
            sql += " AND private = 0"
        if not include_archived:
            sql += " AND archived = 0"
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        with self._lock:
            return [self._item(r) for r in self._db.execute(sql + " ORDER BY updated DESC LIMIT ?", (*args, limit)).fetchall()]

    def _index_item(self, it: dict) -> None:
        row = self._db.execute("SELECT rowid FROM items WHERE id = ?", (it["id"],)).fetchone()
        self._db.execute("DELETE FROM items_fts WHERE rowid = ?", (row[0],))
        self._db.execute("INSERT INTO items_fts(rowid, title, body, summary, tags) VALUES (?,?,?,?,?)", (row[0], it["title"], it["body"], it["summary"], " ".join(it["tags"])))

    def add_item(self, kind: str, title: str, body: str = "", *, authored: str = "you", status: str = "unverified", summary: str = "", project: str = "", tags: Iterable[str] = (),
                 private: bool = False, attrs: dict | None = None, sources: Iterable[str | tuple[str, str]] = (), actor: str = "you", outside: bool = False) -> dict:
        """File a piece of knowledge. `outside` (this turn read a page, mail or file) forces authored=outside and unverified: such content is never "yours" or "verified"."""
        if kind in ROUTED or kind == "decision":
            raise KnowledgeError(f"A {kind} belongs in the world model (world_note / world_decide); link to it with world:NAME instead of copying it here.")
        kind = kind if kind in KINDS else "note"
        authored = authored if authored in AUTHORED else "minty"
        status = status if status in STATUS else "unverified"
        if outside:
            authored, status = "outside", "unverified"
        title = clean(title, LIMITS["title"], "the title")
        if not title:
            raise KnowledgeError("An item needs a title.")
        body = clean(body, LIMITS["body"], "the content", keep_lines=True)
        summary = clean(summary, LIMITS["summary"], "the summary", keep_lines=True)
        tags_ = _tags(tags)
        now = self._clock()
        with self._lock:
            iid = self._id("i", "items")
            self._db.execute("INSERT INTO items (id, kind, title, body, summary, authored, status, created, updated, reviewed, project, tags, private, attrs) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (iid, kind, title, body, summary, authored, status, now, now, now if status == "verified" else 0, clean(project, LIMITS["project"], "the project"),
                              json.dumps(tags_), 1 if private else 0, json.dumps(attrs or {}, ensure_ascii=False)))
            it = self.item(iid)
            self._index_item(it)
            for s in sources:
                sid, quote = (s, "") if isinstance(s, str) else (s[0], s[1])
                if self.source(sid) is not None:
                    self._db.execute("INSERT OR IGNORE INTO evidence (item, source, quote, created) VALUES (?,?,?,?)", (iid, sid, clean(quote, LIMITS["quote"], "the quote"), now))
            self._log(f"item:{iid}", actor, "added", {"kind": kind, "authored": authored})
            self._db.commit()
        return self.item(iid)

    def update_item(self, ident: str, *, actor: str = "you", outside: bool = False, **fields) -> dict:
        """Change an item. Every change is kept in its history with what it was; `status` goes through `set_status`, never straight here."""
        allowed = {"title", "body", "summary", "kind", "project", "tags", "private", "attrs"}
        bad = set(fields) - allowed
        if bad:
            raise KnowledgeError(f"Cannot change {', '.join(sorted(bad))} this way.")
        with self._lock:
            it = self.item(ident)
            if it is None:
                raise KnowledgeError("There is no such item.")
            changes: dict[str, list] = {}
            for k, v in fields.items():
                if v is None:
                    continue
                if k == "title":
                    v = clean(v, LIMITS["title"], "the title")
                    if not v:
                        raise KnowledgeError("An item needs a title.")
                elif k in ("body", "summary"):
                    v = clean(v, LIMITS[k], k, keep_lines=True)
                elif k == "kind":
                    if v in ROUTED or v == "decision":
                        raise KnowledgeError(f"A {v} belongs in the world model.")
                    v = v if v in KINDS else it["kind"]
                elif k == "project":
                    v = clean(v, LIMITS["project"], "the project")
                elif k == "tags":
                    v = _tags(v)
                elif k == "private":
                    v = bool(v)
                if v != it[k]:
                    changes[k] = [it[k] if k not in ("body", "summary") else it[k][:300], v if k not in ("body", "summary") else v[:300]]
                    it[k] = v
            if not changes:
                return it
            if outside and ("body" in changes or "title" in changes):
                it["authored"], it["status"] = "outside", "unverified"  # outside content edited the claim: it is no longer what you said or checked
                changes["authored"] = changes["status"] = ["", "outside"]
            elif "body" in changes and it["authored"] == "you" and actor != "you":
                it["authored"] = "minty"  # the words are no longer only yours
                changes["authored"] = ["you", "minty"]
            if "body" in changes and it["status"] == "verified":
                it["status"] = "unverified"  # new words, not the ones that were checked
                changes["status"] = ["verified", "unverified"]
            it["updated"] = self._clock()
            self._db.execute("UPDATE items SET kind=?, title=?, body=?, summary=?, authored=?, status=?, updated=?, project=?, tags=?, private=?, attrs=? WHERE id=?",
                             (it["kind"], it["title"], it["body"], it["summary"], it["authored"], it["status"], it["updated"], it["project"], json.dumps(it["tags"]), 1 if it["private"] else 0,
                              json.dumps(it["attrs"], ensure_ascii=False), ident))
            self._index_item(it)
            self._log(f"item:{ident}", actor, "edited", changes)
            self._db.commit()
        return self.item(ident)

    def set_status(self, ident: str, status: str, *, actor: str = "you", outside: bool = False, note: str = "") -> dict:
        """verified needs evidence (a source) unless the owner says so, and cannot be set from a turn that read outside content."""
        if status not in STATUS:
            raise KnowledgeError(f"Status is one of {', '.join(STATUS)}.")
        with self._lock:
            it = self.item(ident)
            if it is None:
                raise KnowledgeError("There is no such item.")
            if status == "verified":
                if outside:
                    raise KnowledgeError("This turn read outside content, so it cannot mark anything verified. Ask the owner to check it.")
                if actor != "you" and not self.evidence(ident):
                    raise KnowledgeError("Nothing to verify it against: link a source first, or ask the owner to confirm it.")
            now = self._clock()
            self._db.execute("UPDATE items SET status = ?, reviewed = ?, updated = ? WHERE id = ?", (status, now, now, ident))
            self._log(f"item:{ident}", actor, status, {"note": note[:200]} if note else {})
            self._db.commit()
        return self.item(ident)

    def mark_reviewed(self, ident: str, actor: str = "you") -> None:
        with self._lock:
            self._db.execute("UPDATE items SET reviewed = ? WHERE id = ?", (self._clock(), ident))
            self._log(f"item:{ident}", actor, "reviewed")
            self._db.commit()

    def archive(self, ident: str, archived: bool = True, actor: str = "you") -> bool:
        """Nothing a model can call deletes knowledge: archiving hides it from every view and keeps it, with its history."""
        with self._lock:
            n = self._db.execute("UPDATE items SET archived = ?, updated = ? WHERE id = ?", (1 if archived else 0, self._clock(), ident)).rowcount
            if n:
                self._log(f"item:{ident}", actor, "archived" if archived else "restored")
            self._db.commit()
        return bool(n)

    def evidence(self, ident: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT e.source, e.quote, s.kind, s.title, s.uri, s.origin, s.captured FROM evidence e JOIN sources s ON s.id = e.source WHERE e.item = ? ORDER BY e.created", (ident,)).fetchall()
        return [dict(zip(("source", "quote", "kind", "title", "uri", "origin", "captured"), r)) for r in rows]

    def cite(self, ident: str, source: str, quote: str = "", actor: str = "you") -> bool:
        with self._lock:
            if self.item(ident) is None or self.source(source) is None:
                return False
            self._db.execute("INSERT OR IGNORE INTO evidence (item, source, quote, created) VALUES (?,?,?,?)", (ident, source, clean(quote, LIMITS["quote"], "the quote"), self._clock()))
            self._log(f"item:{ident}", actor, "cited", {"source": source})
            self._db.commit()
        return True

    # ---- links -----------------------------------------------------------------------------------------------------
    def _check_ref(self, ref: str) -> str:
        system, ident = parse_ref(ref)
        if system == "item" and self.item(ident) is None:
            raise KnowledgeError(f"There is no item {ident}.")
        if system == "source" and self.source(ident) is None:
            raise KnowledgeError(f"There is no source {ident}.")
        return f"{system}:{ident}"

    def link(self, src: str, rel: str, dst: str, *, why: str = "", status: str = "confirmed", by: str = "you", score: float = 0.0, outside: bool = False) -> dict:
        """Connect two things. From a turn that read outside content a link is only ever a suggestion; a link to itself is refused; the same link twice is one link;
        a link the owner rejected does not come back unless the owner makes it again."""
        src, dst = self._check_ref(src), self._check_ref(dst)
        if src == dst:
            raise KnowledgeError("Something cannot be linked to itself.")
        rel = clean(rel, 40, "the relation").lower() or "relates to"
        status = status if status in LINK_STATUS else "suggested"
        if outside:
            status = "suggested"
        why = clean(why, LIMITS["why"], "the reason")
        now = self._clock()
        with self._lock:
            row = self._db.execute("SELECT id, status FROM links WHERE src = ? AND rel = ? AND dst = ?", (src, rel, dst)).fetchone()
            if row:
                if (row[1] == "rejected" and by != "you") or row[1] == "confirmed" or (status == "suggested" and row[1] == "suggested"):
                    return {"id": row[0], "src": src, "rel": rel, "dst": dst, "status": row[1], "duplicate": True}
                self._db.execute("UPDATE links SET status = ?, by = ?, why = ?, updated = ? WHERE id = ?", (status, by, why, now, row[0]))
                lid = row[0]
            else:
                lid = self._db.execute("INSERT INTO links (src, rel, dst, why, status, by, score, created, updated) VALUES (?,?,?,?,?,?,?,?,?)",
                                       (src, rel, dst, why, status, by, round(float(score), 3), now, now)).lastrowid
            self._log(src, by, f"linked ({status})", {"rel": rel, "to": dst})
            self._db.commit()
        return {"id": lid, "src": src, "rel": rel, "dst": dst, "status": status, "duplicate": False}

    def set_link(self, lid: int, status: str, actor: str = "you", outside: bool = False) -> dict:
        if status not in LINK_STATUS:
            raise KnowledgeError("A link is confirmed, suggested or rejected.")
        if outside and status == "confirmed":
            raise KnowledgeError("This turn read outside content, so it cannot confirm a link. Ask the owner.")
        with self._lock:
            r = self._db.execute("SELECT src, rel, dst FROM links WHERE id = ?", (lid,)).fetchone()
            if not r:
                raise KnowledgeError("There is no such link.")
            self._db.execute("UPDATE links SET status = ?, updated = ?, by = CASE WHEN ? = 'confirmed' THEN ? ELSE by END WHERE id = ?", (status, self._clock(), status, actor, lid))
            self._log(r[0], actor, f"link {status}", {"rel": r[1], "to": r[2]})
            self._db.commit()
        return {"id": lid, "src": r[0], "rel": r[1], "dst": r[2], "status": status}

    def unlink(self, src: str, rel: str, dst: str, actor: str = "you") -> bool:
        with self._lock:
            n = self._db.execute("UPDATE links SET status = 'rejected', updated = ? WHERE src = ? AND rel = ? AND dst = ?", (self._clock(), src, rel.lower(), dst)).rowcount
            if n:
                self._log(src, actor, "link rejected", {"rel": rel, "to": dst})
            self._db.commit()
        return bool(n)

    @staticmethod
    def _link(r) -> dict:
        return dict(zip(("id", "src", "rel", "dst", "why", "status", "by", "score", "created"), r))

    def links(self, ref: str | None = None, statuses: tuple[str, ...] = ("confirmed",)) -> list[dict]:
        marks = ",".join("?" for _ in statuses)
        sql = f"SELECT id, src, rel, dst, why, status, by, score, created FROM links WHERE status IN ({marks})"  # noqa: S608
        args: list = [*statuses]
        if ref:
            sql += " AND (src = ? OR dst = ?)"
            args += [ref, ref]
        with self._lock:
            return [self._link(r) for r in self._db.execute(sql + " ORDER BY id", args).fetchall()]

    # ---- captures: the inbox ----------------------------------------------------------------------------------------
    @staticmethod
    def _capture(r) -> dict:
        keys = ("id", "created", "updated", "channel", "kind_hint", "origin", "raw", "source_id", "status", "result", "error", "attempts", "private", "meta")
        d = dict(zip(keys, r))
        d["result"], d["meta"], d["private"] = json.loads(d["result"] or "{}"), json.loads(d["meta"] or "{}"), bool(d["private"])
        return d

    _CCOLS = "id, created, updated, channel, kind_hint, origin, raw, source_id, status, result, error, attempts, private, meta"

    def add_capture(self, raw: str, *, channel: str = "", kind_hint: str = "", origin: str = "you", private: bool = False, meta: dict | None = None) -> dict:
        raw = clean(raw, LIMITS["raw"], "the capture", keep_lines=True)
        if not raw:
            raise KnowledgeError("There is nothing to capture.")
        now = self._clock()
        with self._lock:
            cid = self._id("c", "captures")
            self._db.execute("INSERT INTO captures (id, created, updated, channel, kind_hint, origin, raw, private, meta) VALUES (?,?,?,?,?,?,?,?,?)",
                             (cid, now, now, channel[:40], kind_hint[:30], origin if origin in AUTHORED else "outside", raw, 1 if private else 0, json.dumps(meta or {}, ensure_ascii=False)))
            self._log(f"capture:{cid}", origin, "captured", {"channel": channel})
            self._db.commit()
        return self.capture(cid)

    def capture(self, cid: str) -> dict | None:
        with self._lock:
            r = self._db.execute(f"SELECT {self._CCOLS} FROM captures WHERE id = ?", (cid,)).fetchone()  # noqa: S608
        return self._capture(r) if r else None

    def captures(self, status: str = "", include_private: bool = False, limit: int = 200) -> list[dict]:
        sql, args = f"SELECT {self._CCOLS} FROM captures WHERE 1=1", []  # noqa: S608
        if status:
            sql += " AND status = ?"
            args.append(status)
        else:
            sql += " AND status != 'archived'"
        if not include_private:
            sql += " AND private = 0"
        with self._lock:
            return [self._capture(r) for r in self._db.execute(sql + " ORDER BY created DESC LIMIT ?", (*args, limit)).fetchall()]

    def update_capture(self, cid: str, *, actor: str = "you", **fields) -> dict:
        allowed = {"raw", "status", "result", "error", "attempts", "source_id", "kind_hint", "private", "meta"}
        if set(fields) - allowed:
            raise KnowledgeError("Cannot change that on a capture.")
        with self._lock:
            if self.capture(cid) is None:
                raise KnowledgeError("There is no such capture.")
            if "status" in fields and fields["status"] not in CAPTURE_STATUS:
                raise KnowledgeError(f"A capture is {', '.join(CAPTURE_STATUS)}.")
            before = {}
            if "raw" in fields:
                fields["raw"] = clean(fields["raw"], LIMITS["raw"], "the capture", keep_lines=True)
                before = {"raw_before": self.capture(cid)["raw"][:500]}  # editing the input keeps what it was
            sets, args = [], []
            for k, v in fields.items():
                sets.append(f"{k} = ?")
                args.append(json.dumps(v, ensure_ascii=False) if k in ("result", "meta") else (1 if v is True else 0 if v is False else v))
            self._db.execute(f"UPDATE captures SET {', '.join(sets)}, updated = ? WHERE id = ?", (*args, self._clock(), cid))  # noqa: S608
            self._log(f"capture:{cid}", actor, "changed", {**{k: str(v)[:80] for k, v in fields.items() if k not in ("result", "raw")}, **before})
            self._db.commit()
        return self.capture(cid)

    def delete_capture(self, cid: str, actor: str = "you") -> bool:
        """The owner removing an inbox entry for good (panel/API only: no tool calls this). What was already filed from it stays, with its source."""
        with self._lock:
            n = self._db.execute("DELETE FROM captures WHERE id = ?", (cid,)).rowcount
            if n:
                self._log(f"capture:{cid}", actor, "deleted")
            self._db.commit()
        return bool(n)

    # ---- search ----------------------------------------------------------------------------------------------------
    @staticmethod
    def _match(query: str) -> str:
        terms: list[str] = []
        for w in words(query)[:14]:
            if w not in terms:
                terms.append(w)
        return " OR ".join(f'"{t}"' for t in terms)

    def search_items(self, query: str, limit: int = 12, *, include_private: bool = False, include_archived: bool = False) -> list[tuple[str, float]]:
        match = self._match(query)
        if not match:
            return []
        sql = ("SELECT i.id, bm25(items_fts, 4.0, 1.0, 2.0, 2.0) FROM items_fts JOIN items i ON i.rowid = items_fts.rowid WHERE items_fts MATCH ?"
               + ("" if include_private else " AND i.private = 0") + ("" if include_archived else " AND i.archived = 0") + " ORDER BY 2 LIMIT ?")
        with self._lock:
            try:
                return [(i, -r) for i, r in self._db.execute(sql, (match, limit)).fetchall()]
            except sqlite3.OperationalError:
                return []

    def search_sources(self, query: str, limit: int = 8, *, include_private: bool = False) -> list[tuple[str, float]]:
        match = self._match(query)
        if not match:
            return []
        sql = ("SELECT s.id, bm25(sources_fts, 3.0, 1.0) FROM sources_fts JOIN sources s ON s.rowid = sources_fts.rowid WHERE sources_fts MATCH ? AND s.state = 'kept'"
               + ("" if include_private else " AND s.private = 0") + " ORDER BY 2 LIMIT ?")
        with self._lock:
            try:
                return [(i, -r) for i, r in self._db.execute(sql, (match, limit)).fetchall()]
            except sqlite3.OperationalError:
                return []

    def items_citing(self, source: str) -> list[str]:
        with self._lock:
            return [r[0] for r in self._db.execute("SELECT item FROM evidence WHERE source = ?", (source,)).fetchall()]

    def stats(self, include_private: bool = False) -> dict:
        priv = "" if include_private else " WHERE private = 0"
        with self._lock:
            kinds = dict(self._db.execute(f"SELECT kind, COUNT(*) FROM items{priv}{' AND' if priv else ' WHERE'} archived = 0 GROUP BY kind").fetchall())  # noqa: S608
            caps = dict(self._db.execute("SELECT status, COUNT(*) FROM captures WHERE status != 'archived' GROUP BY status").fetchall())
            n_src = self._db.execute(f"SELECT COUNT(*) FROM sources{priv}").fetchone()[0]  # noqa: S608
            n_link = self._db.execute("SELECT COUNT(*) FROM links WHERE status = 'confirmed'").fetchone()[0]
            n_sug = self._db.execute("SELECT COUNT(*) FROM links WHERE status = 'suggested'").fetchone()[0]
        return {"items": sum(kinds.values()), "by_kind": kinds, "captures": caps, "sources": n_src, "links": n_link, "suggested_links": n_sug}
