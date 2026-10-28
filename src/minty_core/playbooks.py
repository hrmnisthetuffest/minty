"""Reusable ways of doing a kind of work: versioned, measured, adapted to the facts, and never replaced by one bad run.

A playbook is plain text for the mission worker: what a good result of this kind looks like (the context it needs, the steps, the output,
the checks, what to do when it goes wrong), found for a goal by its trigger words. It is background, never a permission: every step still
goes through the safety layer, and what goes in is plain text (no links, nothing that reads as an order to a model). An improvement is a
*proposed* version that changes nothing until the user accepts it; every run records how it ended and how long it took, so a version is
judged on numbers and not on its last run, and going back to the one before is a single step. Nothing here rewrites a playbook by itself.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from minty_core.provenance import looks_like_injection

NAME = re.compile(r"[a-z][a-z0-9-]{1,39}")
LIMITS = {"steps": 2000, "context": 300, "output": 300, "checks": 300, "recovery": 300}
_URL = re.compile(r"https?://|www\.", re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9]+")
MIN_TRIGGER_HITS = 2


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def _words(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall(str(text or "").lower())}


@dataclass(frozen=True)
class Version:
    name: str
    version: int
    status: str  # active | proposed | retired
    steps: str
    context: str
    output: str
    checks: str
    recovery: str
    note: str


@dataclass(frozen=True)
class Match:
    name: str
    version: int
    steps: str
    context: str
    output: str
    checks: str
    recovery: str

    def text(self) -> str:
        lines = [f'Playbook "{self.name}" (version {self.version}): a way that has worked for this kind of goal. Adapt it to the facts here '
                 "instead of following it blindly, and say where you left it out."]
        for label, value in (("Context", self.context), ("Steps", self.steps), ("Output", self.output), ("Check", self.checks),
                             ("If it goes wrong", self.recovery)):
            if value:
                lines.append(f"{label}: {value}")
        return "\n".join(lines)


def _field(label: str, text, limit: int, required: bool = False) -> str:
    text = " ".join(str(text or "").split())
    if required and not text:
        raise ValueError(f"A playbook needs its {label}.")
    if len(text) > limit:
        raise ValueError(f"The {label} is too long (at most {limit} characters).")
    if _URL.search(text) or looks_like_injection(text):
        raise ValueError(f"The {label} has to be plain words: no links and nothing that gives orders to a model.")
    return text


class PlaybookStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.close(os.open(path, os.O_CREAT | os.O_RDWR, 0o600))
        self._clock, self._lock = clock, threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS playbooks (name TEXT PRIMARY KEY, triggers TEXT NOT NULL, created REAL NOT NULL);"
            "CREATE TABLE IF NOT EXISTS versions (name TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL, steps TEXT NOT NULL, "
            "context TEXT NOT NULL DEFAULT '', output TEXT NOT NULL DEFAULT '', checks TEXT NOT NULL DEFAULT '', recovery TEXT NOT NULL DEFAULT '', "
            "note TEXT NOT NULL DEFAULT '', created REAL NOT NULL, PRIMARY KEY (name, version));"
            "CREATE TABLE IF NOT EXISTS runs (name TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL, seconds REAL NOT NULL, ts REAL NOT NULL);")
        self._db.commit()

    # ---- making and changing ------------------------------------------------------------------------------------------
    def create(self, name: str, triggers: str, steps: str, context: str = "", output: str = "", checks: str = "", recovery: str = "") -> Version:
        name = str(name or "").strip()
        if not NAME.fullmatch(name):
            raise ValueError("A playbook's name is lowercase words with dashes, like market-research.")
        trigger_words = _words(triggers)
        if len(trigger_words) < MIN_TRIGGER_HITS:
            raise ValueError("Give at least two words that say what kind of goal this is for.")
        fields = {"steps": _field("steps", steps, LIMITS["steps"], True), "context": _field("context", context, LIMITS["context"]),
                  "output": _field("output", output, LIMITS["output"]), "checks": _field("checks", checks, LIMITS["checks"]),
                  "recovery": _field("recovery", recovery, LIMITS["recovery"])}
        now = self._clock()
        with self._lock:
            if self._db.execute("SELECT 1 FROM playbooks WHERE name = ?", (name,)).fetchone():
                raise ValueError(f"There is already a playbook called {name}.")
            self._db.execute("INSERT INTO playbooks (name, triggers, created) VALUES (?,?,?)", (name, " ".join(sorted(trigger_words)), now))
            self._db.execute("INSERT INTO versions (name, version, status, steps, context, output, checks, recovery, created) VALUES (?,1,'active',?,?,?,?,?,?)",
                             (name, fields["steps"], fields["context"], fields["output"], fields["checks"], fields["recovery"], now))
            self._db.commit()
        return self._version(name, 1)

    def propose(self, name: str, steps: str | None = None, context: str | None = None, output: str | None = None, checks: str | None = None,
                recovery: str | None = None, note: str = "") -> Version:
        """An improvement: a new version that changes nothing until it is accepted."""
        with self._lock:
            base = self._active(name)
            if base is None:
                raise ValueError(f"There is no playbook called {name}.")
            new = {"steps": steps, "context": context, "output": output, "checks": checks, "recovery": recovery}
            fields = {k: _field(k, v if v is not None else getattr(base, k), LIMITS[k], k == "steps") for k, v in new.items()}
            number = self._db.execute("SELECT MAX(version) FROM versions WHERE name = ?", (name,)).fetchone()[0] + 1
            self._db.execute("INSERT INTO versions (name, version, status, steps, context, output, checks, recovery, note, created) VALUES (?,?, 'proposed', ?,?,?,?,?,?,?)",
                             (name, number, fields["steps"], fields["context"], fields["output"], fields["checks"], fields["recovery"],
                              _field("note", note, 200), self._clock()))
            self._db.commit()
        return self._version(name, number)

    def accept(self, name: str, version: int) -> bool:
        with self._lock:
            if not self._db.execute("SELECT 1 FROM versions WHERE name = ? AND version = ? AND status = 'proposed'", (name, version)).fetchone():
                return False
            self._db.execute("UPDATE versions SET status = 'retired' WHERE name = ? AND status = 'active'", (name,))
            self._db.execute("UPDATE versions SET status = 'active' WHERE name = ? AND version = ?", (name, version))
            self._db.commit()
        return True

    def rollback(self, name: str) -> bool:
        """Back to the version before the active one: one step, and the later one is kept (retired) in case it was better after all."""
        with self._lock:
            active = self._active(name)
            if active is None:
                return False
            older = self._db.execute("SELECT version FROM versions WHERE name = ? AND status = 'retired' AND version < ? ORDER BY version DESC LIMIT 1",
                                     (name, active.version)).fetchone()
            if older is None:
                return False
            self._db.execute("UPDATE versions SET status = 'retired' WHERE name = ? AND version = ?", (name, active.version))
            self._db.execute("UPDATE versions SET status = 'active' WHERE name = ? AND version = ?", (name, older[0]))
            self._db.commit()
        return True

    def remove(self, name: str) -> bool:
        with self._lock:
            self._db.execute("DELETE FROM runs WHERE name = ?", (name,))
            self._db.execute("DELETE FROM versions WHERE name = ?", (name,))
            cur = self._db.execute("DELETE FROM playbooks WHERE name = ?", (name,))
            self._db.commit()
        return cur.rowcount > 0

    # ---- using and measuring ---------------------------------------------------------------------------------------------
    def match(self, goal: str) -> Match | None:
        words, best = _words(goal), None
        for name, triggers in self._db.execute("SELECT name, triggers FROM playbooks").fetchall():
            hits = len(words & set(triggers.split()))
            if hits >= MIN_TRIGGER_HITS and (best is None or hits > best[0]):
                best = (hits, name)
        if best is None:
            return None
        v = self._active(best[1])
        return Match(v.name, v.version, v.steps, v.context, v.output, v.checks, v.recovery) if v else None

    def record(self, name: str, version: int, status: str, seconds: float = 0.0) -> None:
        status = status if status in ("done", "failed", "blocked") else "failed"
        with self._lock:
            self._db.execute("INSERT INTO runs (name, version, status, seconds, ts) VALUES (?,?,?,?,?)", (name, version, status, max(0.0, float(seconds)), self._clock()))
            self._db.commit()

    def stats(self, name: str) -> list[dict]:
        out = []
        for v in self.versions(name):
            rows = self._db.execute("SELECT status, seconds FROM runs WHERE name = ? AND version = ?", (name, v.version)).fetchall()
            done = sum(1 for s, _ in rows if s == "done")
            out.append({"version": v.version, "status": v.status, "runs": len(rows), "done": done,
                        "success_rate": round(done / len(rows), 2) if rows else 0.0,
                        "avg_seconds": round(sum(sec for _, sec in rows) / len(rows), 1) if rows else 0.0})
        return out

    def compare(self, name: str) -> str:
        parts = []
        for s in self.stats(name):
            if s["status"] == "retired":
                continue
            what = f"{s['runs']} runs, {s['done']} done, about {s['avg_seconds']:g} s" if s["runs"] else "no runs yet"
            parts.append(f"Version {s['version']} ({s['status']}): {what}.")
        return " ".join(parts) or "No such playbook."

    def versions(self, name: str) -> list[Version]:
        rows = self._db.execute("SELECT name, version, status, steps, context, output, checks, recovery, note FROM versions WHERE name = ? ORDER BY version", (name,)).fetchall()
        return [Version(*r) for r in rows]

    def names(self) -> list[str]:
        return [r[0] for r in self._db.execute("SELECT name FROM playbooks ORDER BY name").fetchall()]

    def _active(self, name: str) -> Version | None:
        return next((v for v in self.versions(name) if v.status == "active"), None)

    def _version(self, name: str, number: int) -> Version:
        return next(v for v in self.versions(name) if v.version == number)

    def close(self) -> None:
        with self._lock:
            self._db.close()
