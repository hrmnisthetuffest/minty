"""What Minty knows about the owner's world as connected, sourced facts, and the decisions made in it.

Not messages and not a search index: entities (a project, a person, a document, a task ...), facts about them, relations between them, and decisions with the
assumptions they rest on. Everything has a *provenance*, from most to least trusted: `told` (the owner said it), `verified` (checked), `observed` (seen in a run),
`discovered` (read from an authorised source), `inferred` (concluded by Minty), `assumed` (a working guess), `outdated`. Rules that keep it honest:

* A change adds a new record and marks the old one superseded; nothing is overwritten or deleted, so "what did we believe then" is answerable.
* Two sources that disagree are kept side by side as a **conflict** until the owner says which is right; the owner's word (`told`) always wins and the loser stays on record.
* Something read from mail, a page or a file can only ever be `discovered`, `observed` or `inferred` (`told` and `verified` are refused when the turn read outside content),
  must not read as an order to a model, and is cut to a short length.
* Assumptions and inferences go stale (a few weeks) and then stop being offered as if they were current. Facts the owner told stay until changed.
* Records made while a private turn was open are flagged, and never put into a prompt for a turn that is not private.
Retrieval is by name: only entities the request actually names (or has an alias for) come back, with their neighbours and the decisions that touch them, in a short block.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

from minty_core.provenance import looks_like_injection

RANK = {"verified": 5, "told": 5, "observed": 3, "discovered": 2, "inferred": 1, "assumed": 0, "outdated": -1}
OUTSIDE_OK = ("discovered", "observed", "inferred")  # what content from outside may claim to be
STALE_DAYS = {"assumed": 14, "inferred": 45, "discovered": 120}  # told and verified do not go stale by themselves
LIMITS = {"name": 60, "value": 400, "text": 600}
KINDS = ("project", "task", "person", "organization", "document", "email", "workflow", "goal", "deadline", "place", "topic", "other")
LINKS = ("contains", "depends_on", "blocks", "affects", "relates_to", "about", "belongs_to", "works_at", "produced", "follows_up", "contradicts")
DECISION_KINDS = ("decided", "proposed", "inferred", "assumed", "recommended")
DAY = 86400.0


def _clean(text, limit: int, what: str) -> str:
    one = " ".join(str(text or "").split())
    if len(one) > limit:
        raise ValueError(f"{what} is too long (at most {limit} characters)")
    return one


def _words(text: str) -> set[str]:
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in re.findall(r"[a-z0-9]+", str(text).lower())}


STOP = frozenset("a an the and or of to in on for with is are was were be been do did does why what how when who which that this it we you i our my me us they them about from at by as not no".split())


def _content_words(text: str) -> set[str]:
    """The words of a question that say what it is about: a word like "we" must not match every decision."""
    return {w for w in _words(text) if w not in STOP}


class WorldStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.close(os.open(path, os.O_CREAT | os.O_RDWR, 0o600))
        self._clock, self._lock = clock, threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS entities (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, name TEXT NOT NULL, key TEXT NOT NULL UNIQUE,"
            " status TEXT NOT NULL DEFAULT 'active', created REAL NOT NULL, updated REAL NOT NULL, private INTEGER NOT NULL DEFAULT 0);"
            "CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY AUTOINCREMENT, entity INTEGER NOT NULL, attr TEXT NOT NULL, value TEXT NOT NULL,"
            " provenance TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'current', ts REAL NOT NULL, supersedes INTEGER, private INTEGER NOT NULL DEFAULT 0);"
            "CREATE TABLE IF NOT EXISTS relations (id INTEGER PRIMARY KEY AUTOINCREMENT, a INTEGER NOT NULL, rel TEXT NOT NULL, b INTEGER NOT NULL,"
            " provenance TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'current', ts REAL NOT NULL, private INTEGER NOT NULL DEFAULT 0);"
            "CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, kind TEXT NOT NULL, rationale TEXT NOT NULL DEFAULT '',"
            " alternatives TEXT NOT NULL DEFAULT '', assumptions TEXT NOT NULL DEFAULT '', expected TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',"
            " status TEXT NOT NULL DEFAULT 'current', supersedes INTEGER, outcome TEXT NOT NULL DEFAULT '', outcome_result TEXT NOT NULL DEFAULT '', entities TEXT NOT NULL DEFAULT '[]',"
            " ts REAL NOT NULL, private INTEGER NOT NULL DEFAULT 0);")
        self._db.commit()

    # ---- entities --------------------------------------------------------------------------------------------------
    def entity(self, kind: str, name: str, create: bool = True, private: bool = False) -> int | None:
        name = _clean(name, LIMITS["name"], "a name")
        kind = kind if kind in KINDS else "other"
        key = f"{kind}:{name.lower()}"
        now = self._clock()
        with self._lock:
            row = self._db.execute("SELECT id FROM entities WHERE key = ?", (key,)).fetchone()
            if row:
                return row[0]
            if not name or not create:
                return None
            cur = self._db.execute("INSERT INTO entities (kind, name, key, created, updated, private) VALUES (?,?,?,?,?,?)", (kind, name, key, now, now, 1 if private else 0))
            self._db.commit()
            return cur.lastrowid

    def _find(self, name: str) -> int | None:
        """An entity by name, whatever its kind (the first match), for linking and recall."""
        with self._lock:
            row = self._db.execute("SELECT id FROM entities WHERE lower(name) = ? ORDER BY id LIMIT 1", (name.strip().lower(),)).fetchone()
        return row[0] if row else None

    # ---- facts: provenance, supersede, conflict ---------------------------------------------------------------------
    def note(self, kind: str, name: str, attr: str, value: str, provenance: str = "inferred", source: str = "", *, outside: bool = False, private: bool = False) -> dict:
        """Record a fact. `outside`: the turn read content from mail, a page or a file, so the fact cannot be `told` or `verified` however it is labelled."""
        attr, value, source = _clean(attr, LIMITS["name"], "the attribute"), _clean(value, LIMITS["value"], "the value"), _clean(source, 120, "the source")
        if provenance not in RANK or provenance == "outdated":
            raise ValueError(f"provenance is one of {', '.join(p for p in RANK if p != 'outdated')}")
        if outside:
            if provenance not in OUTSIDE_OK:
                provenance = "discovered"  # content from outside never gets to say "the owner told me"
            if looks_like_injection(f"{attr} {value}"):
                raise ValueError("that reads like an instruction to a model, not a fact, so it was not kept")
        eid = self.entity(kind, name, private=private)
        now = self._clock()
        with self._lock:
            current = self._db.execute("SELECT id, value, provenance FROM facts WHERE entity = ? AND attr = ? AND status IN ('current','conflict') ORDER BY id", (eid, attr)).fetchall()
            for fid, old_value, old_provenance in current:
                if old_value.lower() == value.lower():  # the same thing again: fresher, and the better source wins
                    best = provenance if RANK[provenance] > RANK.get(old_provenance, 0) else old_provenance
                    self._db.execute("UPDATE facts SET ts = ?, provenance = ? WHERE id = ?", (now, best, fid))
                    self._db.commit()
                    return {"entity": eid, "fact": fid, "status": "already known", "conflicts": 0, "provenance": best}
            supersedes = None
            status = "current"
            if current:
                if RANK[provenance] >= RANK["told"]:  # the owner's word (or a verification) settles it: the older ones stay on record as superseded
                    for fid, _v, _p in current:
                        self._db.execute("UPDATE facts SET status = 'superseded' WHERE id = ?", (fid,))
                    supersedes = current[-1][0]
                else:  # a disagreement nobody has settled: it stays marked, and the owner's word, if there is one, stays current
                    status = "conflict"
                    if max(RANK[p] for _f, _v, p in current) < RANK["told"]:
                        for fid, _v, _p in current:
                            self._db.execute("UPDATE facts SET status = 'conflict' WHERE id = ?", (fid,))
            cur = self._db.execute("INSERT INTO facts (entity, attr, value, provenance, source, status, ts, supersedes, private) VALUES (?,?,?,?,?,?,?,?,?)",
                                   (eid, attr, value, provenance, source, status, now, supersedes, 1 if private else 0))
            self._db.execute("UPDATE entities SET updated = ?, private = CASE WHEN ? THEN private ELSE 0 END WHERE id = ?", (now, 1 if private else 0, eid))  # visible once any fact about it is not private
            self._db.commit()
            conflicts = self._db.execute("SELECT COUNT(*) FROM facts WHERE entity = ? AND attr = ? AND status = 'conflict'", (eid, attr)).fetchone()[0]
        return {"entity": eid, "fact": cur.lastrowid, "status": status, "conflicts": conflicts, "provenance": provenance}

    def relate(self, a_kind: str, a_name: str, rel: str, b_kind: str, b_name: str, provenance: str = "inferred", source: str = "", *, outside: bool = False, private: bool = False) -> dict:
        if rel not in LINKS:
            raise ValueError(f"a relation is one of {', '.join(LINKS)}")
        if outside and provenance not in OUTSIDE_OK:
            provenance = "discovered"
        a, b = self.entity(a_kind, a_name, private=private), self.entity(b_kind, b_name, private=private)
        now = self._clock()
        with self._lock:
            if self._db.execute("SELECT 1 FROM relations WHERE a = ? AND rel = ? AND b = ? AND status = 'current'", (a, rel, b)).fetchone():
                return {"status": "already known"}
            cur = self._db.execute("INSERT INTO relations (a, rel, b, provenance, source, ts, private) VALUES (?,?,?,?,?,?,?)", (a, rel, b, provenance, _clean(source, 120, "the source"), now, 1 if private else 0))
            self._db.commit()
        return {"relation": cur.lastrowid, "status": "current"}

    def end_relation(self, a_name: str, rel: str, b_name: str) -> int:
        a, b = self._find(a_name), self._find(b_name)
        with self._lock:
            cur = self._db.execute("UPDATE relations SET status = 'ended' WHERE a = ? AND rel = ? AND b = ? AND status = 'current'", (a, rel, b))
            self._db.commit()
        return cur.rowcount

    # ---- freshness ---------------------------------------------------------------------------------------------------
    def _stale(self, provenance: str, ts: float, now: float) -> bool:
        days = STALE_DAYS.get(provenance)
        return days is not None and now - ts > days * DAY

    def refresh(self) -> int:
        """Mark expired assumptions and inferences `outdated`: they stay on record, and stop being offered as current."""
        now, n = self._clock(), 0
        with self._lock:
            for fid, prov, ts in self._db.execute("SELECT id, provenance, ts FROM facts WHERE status = 'current' AND provenance IN ('assumed','inferred')").fetchall():
                if self._stale(prov, ts, now):
                    self._db.execute("UPDATE facts SET status = 'outdated' WHERE id = ?", (fid,))
                    n += 1
            self._db.commit()
        return n

    # ---- reading ---------------------------------------------------------------------------------------------------
    def _entity_facts(self, eid: int, include_private: bool) -> list[dict]:
        rows = self._db.execute("SELECT attr, value, provenance, source, status, ts, private FROM facts WHERE entity = ? AND status IN ('current','conflict') ORDER BY id DESC", (eid,)).fetchall()
        now = self._clock()
        shown = [(RANK[r[2]], r[4] == "conflict", {"attr": r[0], "value": r[1], "provenance": r[2], "source": r[3], "status": r[4], "age_days": int((now - r[5]) / DAY),
                                                   "stale": self._stale(r[2], r[5], now)}) for r in rows if include_private or not r[6]]
        shown.sort(key=lambda t: (not t[1], -t[0]))  # what is unsettled first, then the better sources; newest first within the same kind
        return [t[2] for t in shown]

    def _links(self, eid: int, include_private: bool) -> list[dict]:
        out = []
        for a, rel, b, prov, priv in self._db.execute("SELECT a, rel, b, provenance, private FROM relations WHERE (a = ? OR b = ?) AND status = 'current'", (eid, eid)).fetchall():
            if priv and not include_private:
                continue
            other = b if a == eid else a
            name = self._db.execute("SELECT name FROM entities WHERE id = ?", (other,)).fetchone()
            out.append({"rel": rel if a == eid else f"is {rel} of" if rel in ("contains", "blocks", "affects") else f"{rel} (reverse)", "with": name[0] if name else "?", "provenance": prov})
        return out

    def match(self, request: str, include_private: bool = True) -> list[int]:
        """The entities a request names: by name or by an alias fact, whole words only."""
        words = _words(request)
        if not words:
            return []
        found: list[int] = []
        with self._lock:
            for eid, name, priv in self._db.execute("SELECT id, name, private FROM entities WHERE status = 'active'").fetchall():
                if priv and not include_private:
                    continue
                nameset = _words(name)
                if len(name) >= 3 and nameset and nameset <= words:
                    found.append(eid)
            for eid, value in self._db.execute("SELECT entity, value FROM facts WHERE attr = 'alias' AND status IN ('current','conflict')").fetchall():
                aw = _words(value)
                if aw and aw <= words and eid not in found:
                    found.append(eid)
        return found[:5]

    def recall(self, request: str, *, include_private: bool = True, depth: int = 1) -> dict:
        """What is known about the entities a request names: facts (with conflicts shown, not resolved), links and the decisions that touch them."""
        ids = self.match(request, include_private)
        with self._lock:
            out = []
            for eid in ids:
                row = self._db.execute("SELECT kind, name, status FROM entities WHERE id = ?", (eid,)).fetchone()
                out.append({"entity": row[1], "kind": row[0], "status": row[2], "facts": self._entity_facts(eid, include_private), "links": self._links(eid, include_private)[:8],
                            "decisions": [d for d in self.decisions(entity=eid, include_private=include_private) if d["status"] == "current"][:4]})
        return {"entities": out}

    def hint(self, request: str, *, include_private: bool, cap: int = 700) -> str:
        """The short block that goes in front of the model when a request names something Minty knows: only current, non-stale facts, conflicts flagged, capped."""
        data = self.recall(request, include_private=include_private)["entities"]
        if not data:
            return ""
        lines = []
        for e in data:
            facts = [f for f in e["facts"] if not f["stale"]]
            bits = [f"{f['attr']}: {f['value']} ({f['provenance']})" if f["status"] == "current" else f"CONFLICT {f['attr']}: {f['value']} ({f['provenance']}, {f['source'] or 'no source'})" for f in facts[:6]]
            links = [f"{l['rel']} {l['with']}" for l in e["links"][:4]]
            dec = [f"decided: {d['title']}" if d["kind"] == "decided" else f"{d['kind']}: {d['title']}" for d in e["decisions"][:2]]
            lines.append(f"{e['entity']} ({e['kind']}): " + "; ".join(bits + links + dec))
        text = "What the owner's world already holds about this (sourced; a CONFLICT is unsettled, ask which is right; an assumption is a guess): " + " | ".join(lines)
        return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"

    def impact(self, name: str) -> dict:
        """What may need a second look if this changed: the things that depend on it, contain it or are affected by it, and the decisions that touch it."""
        eid = self._find(name)
        if eid is None:
            return {"entity": name, "found": False}
        seen: set[int] = set()
        todo = [eid]
        with self._lock:
            while todo:
                cur = todo.pop()
                for a, rel in self._db.execute("SELECT a, rel FROM relations WHERE b = ? AND status = 'current' AND rel IN ('depends_on','belongs_to','about','follows_up')", (cur,)).fetchall():
                    if a not in seen:
                        seen.add(a)
                        todo.append(a)
                for b in [r[0] for r in self._db.execute("SELECT b FROM relations WHERE a = ? AND status = 'current' AND rel IN ('affects','blocks','contains')", (cur,)).fetchall()]:
                    if b not in seen:
                        seen.add(b)
                        todo.append(b)
            names = [self._db.execute("SELECT name, kind FROM entities WHERE id = ?", (i,)).fetchone() for i in sorted(seen)]
            decs = self.decisions(entity=eid)
        return {"entity": name, "found": True, "may_be_affected": [f"{n} ({k})" for n, k in names], "decisions_that_touch_it": [d["title"] for d in decs if d["status"] == "current"]}

    def conflicts(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT e.name, f.attr, f.value, f.provenance, f.source FROM facts f JOIN entities e ON e.id = f.entity WHERE f.status = 'conflict' ORDER BY e.name, f.attr, f.id").fetchall()
        return [{"entity": r[0], "attr": r[1], "value": r[2], "provenance": r[3], "source": r[4]} for r in rows]

    # ---- decisions and assumptions -----------------------------------------------------------------------------------
    def decide(self, title: str, kind: str = "decided", rationale: str = "", alternatives: str = "", assumptions: str = "", expected: str = "", source: str = "", entities: list[str] | None = None,
               *, outside: bool = False, private: bool = False, supersedes: int | None = None) -> int:
        """A decision, or something that must not be confused with one: a proposal, an inference, an assumption, a recommendation nobody accepted."""
        if kind not in DECISION_KINDS:
            raise ValueError(f"kind is one of {', '.join(DECISION_KINDS)}")
        if outside and kind == "decided":
            kind = "proposed"  # only the owner decides; content from outside can at most propose
        fields = [_clean(x, LIMITS["text"], w) for x, w in ((title, "the title"), (rationale, "the rationale"), (alternatives, "the alternatives"), (assumptions, "the assumptions"), (expected, "what is expected"))]
        if outside and any(looks_like_injection(f) for f in fields):
            raise ValueError("that reads like an instruction to a model, not a decision, so it was not kept")
        ids = [e for e in (self._find(n) for n in (entities or [])) if e is not None]
        with self._lock:
            if supersedes is not None:
                self._db.execute("UPDATE decisions SET status = 'superseded' WHERE id = ?", (int(supersedes),))
            cur = self._db.execute("INSERT INTO decisions (title, kind, rationale, alternatives, assumptions, expected, source, supersedes, entities, ts, private) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                   (fields[0], kind, fields[1], fields[2], fields[3], fields[4], _clean(source, 120, "the source"), supersedes, json.dumps(ids), self._clock(), 1 if private else 0))
            self._db.commit()
            return cur.lastrowid

    def outcome(self, decision_id: int, text: str, result: str) -> bool:
        if result not in ("good", "bad", "mixed", "wrong"):
            raise ValueError("result is good, bad, mixed or wrong (an assumption that proved wrong)")
        with self._lock:
            cur = self._db.execute("UPDATE decisions SET outcome = ?, outcome_result = ? WHERE id = ?", (_clean(text, LIMITS["text"], "the outcome"), result, int(decision_id)))
            self._db.commit()
        return cur.rowcount == 1

    def decisions(self, query: str = "", entity: int | None = None, include_private: bool = True, limit: int = 8) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT id, title, kind, rationale, alternatives, assumptions, expected, source, status, supersedes, outcome, outcome_result, entities, ts, private FROM decisions ORDER BY id DESC").fetchall()
        words, out = _content_words(query), []
        for r in rows:
            if r[14] and not include_private:
                continue
            if entity is not None and entity not in json.loads(r[12] or "[]"):
                continue
            score = len(words & _content_words(f"{r[1]} {r[3]} {r[4]} {r[5]}"))
            if words and not score:
                continue
            out.append({"score": score, "id": r[0], "title": r[1], "kind": r[2], "rationale": r[3], "alternatives": r[4], "assumptions": r[5], "expected": r[6], "source": r[7], "status": r[8],
                        "supersedes": r[9], "outcome": r[10], "outcome_result": r[11], "date": time.strftime("%Y-%m-%d", time.localtime(r[13]))})
        out.sort(key=lambda d: (-d["score"], -d["id"]))  # the best match first, newest among equals
        for d in out:
            d.pop("score")
        return out[:limit]

    def wrong_assumptions(self) -> list[str]:
        """Assumptions that later proved wrong: the lesson, so the same guess is trusted less."""
        return [d["title"] for d in self.decisions(limit=200) if d["kind"] == "assumed" and d["outcome_result"] == "wrong"]

    def graph(self, include_private: bool = False) -> dict:
        """Everything the second brain's map needs from the world model: the entities (with their top facts), the current relations between them and the current decisions."""
        with self._lock:
            ents = self._db.execute("SELECT id, kind, name, updated, private FROM entities WHERE status = 'active' ORDER BY id").fetchall()
            facts: dict[int, list[str]] = {}
            for eid, attr, value in self._db.execute("SELECT entity, attr, value FROM facts WHERE status IN ('current','conflict')" + ("" if include_private else " AND private = 0") + " ORDER BY id").fetchall():
                facts.setdefault(eid, []).append(f"{attr}: {value}")
            rels = self._db.execute("SELECT a, rel, b, private FROM relations WHERE status = 'current'").fetchall()
            decs = self._db.execute("SELECT id, title, kind, rationale, entities, ts, private FROM decisions WHERE status = 'current' ORDER BY id").fetchall()
        shown = {e[0]: e[2] for e in ents if include_private or not e[4]}
        return {"entities": [{"kind": k, "name": n, "updated": u, "private": bool(pr), "facts": facts.get(i, [])[:2]} for i, k, n, u, pr in ents if i in shown],
                "relations": [{"a": shown[a], "rel": r, "b": shown[b]} for a, r, b, pr in rels if a in shown and b in shown and (include_private or not pr)],
                "decisions": [{"id": i, "title": t, "kind": k, "rationale": ra, "entities": [shown[e] for e in json.loads(es or "[]") if e in shown], "ts": ts}
                              for i, t, k, ra, es, ts, pr in decs if include_private or not pr]}

    def close(self) -> None:
        with self._lock:
            self._db.close()
