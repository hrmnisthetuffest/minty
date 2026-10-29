"""Where workflows live: definitions with their versions, and every run with every step.

One sqlite file. A workflow has any number of immutable versions; one is active. Editing never changes a version, it adds one (so a run pinned to
version 3 is never changed under its feet, and going back is a new version that copies an old one). Deleting is archiving: nothing is erased. A run
records the version it started on, its trigger, its result and each step's input, output, timing and error, with anything that looks like a secret
masked and long values cut. All SQL here is constant text.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SECRET_KEY = re.compile(r"key|token|secret|passw|authorization|credential|cookie", re.IGNORECASE)
CUT = 4000
KEEP_RUNS = 300


def redact(value, depth: int = 0):
    """A copy that is safe to keep and show: secret-looking fields masked, long text cut."""
    if depth > 8:
        return "…"
    if isinstance(value, dict):
        return {str(k): ("[hidden]" if SECRET_KEY.search(str(k)) and v not in (None, "") else redact(v, depth + 1)) for k, v in list(value.items())[:100]}
    if isinstance(value, list):
        return [redact(v, depth + 1) for v in value[:100]]
    if isinstance(value, str) and len(value) > CUT:
        return value[:CUT] + f"… ({len(value) - CUT} more characters)"
    return value


def _dump(value) -> str:
    text = json.dumps(redact(value), ensure_ascii=False, default=str)
    return text if len(text) <= 20000 else json.dumps({"truncated": text[:2000]})


def _load(text):
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


@dataclass(frozen=True)
class Flow:
    id: int
    name: str
    creator: str
    enabled: bool
    archived: bool
    active_version: int
    created: float
    updated: float


_FLOW = "id, name, creator, enabled, archived, active_version, created, updated"
_RUN = "id, flow_id, version, mode, trigger, status, started, finished, input, output, error, verified, metrics, job_id"


class FlowStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.close(os.open(path, os.O_CREAT | os.O_RDWR, 0o600))
        self._clock, self._lock = clock, threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS flows (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE, creator TEXT NOT NULL,"
            " enabled INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0, active_version INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL);"
            "CREATE TABLE IF NOT EXISTS versions (flow_id INTEGER NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL, doc TEXT NOT NULL,"
            " note TEXT NOT NULL DEFAULT '', by TEXT NOT NULL DEFAULT '', created REAL NOT NULL, PRIMARY KEY (flow_id, version));"
            "CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, flow_id INTEGER NOT NULL, version INTEGER NOT NULL, mode TEXT NOT NULL,"
            " trigger TEXT NOT NULL DEFAULT 'manual', trigger_key TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, started REAL NOT NULL, finished REAL,"
            " input TEXT, output TEXT, error TEXT, verified INTEGER NOT NULL DEFAULT 0, metrics TEXT, job_id INTEGER);"
            "CREATE UNIQUE INDEX IF NOT EXISTS runs_key ON runs (flow_id, trigger_key) WHERE trigger_key != '';"
            "CREATE TABLE IF NOT EXISTS steps (run_id INTEGER NOT NULL, node_id TEXT NOT NULL, status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1,"
            " started REAL, finished REAL, ms INTEGER, input TEXT, output TEXT, error TEXT, effect TEXT, PRIMARY KEY (run_id, node_id));")
        self._db.commit()

    # ---- definitions ---------------------------------------------------------------------------------------------
    def _flow(self, row) -> Flow:
        return Flow(row[0], row[1], row[2], bool(row[3]), bool(row[4]), row[5], row[6], row[7])

    def get(self, ref) -> Flow | None:
        """By id or by name (any case)."""
        with self._lock:
            if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
                row = self._db.execute("SELECT id, name, creator, enabled, archived, active_version, created, updated FROM flows WHERE id = ?", (int(ref),)).fetchone()
            else:
                row = self._db.execute("SELECT id, name, creator, enabled, archived, active_version, created, updated FROM flows WHERE name = ?", (str(ref).strip(),)).fetchone()
        return self._flow(row) if row else None

    def list(self, include_archived: bool = False) -> list[Flow]:
        with self._lock:
            rows = self._db.execute("SELECT id, name, creator, enabled, archived, active_version, created, updated FROM flows ORDER BY id").fetchall()
        return [self._flow(r) for r in rows if include_archived or not r[4]]

    def create(self, doc: dict, creator: str = "user", by: str = "", note: str = "") -> Flow:
        name = str(doc.get("name") or "").strip()
        now = self._clock()
        with self._lock:
            if self._db.execute("SELECT 1 FROM flows WHERE name = ?", (name,)).fetchone():
                raise ValueError(f"There is already a workflow called {name}.")
            cur = self._db.execute("INSERT INTO flows (name, creator, enabled, archived, active_version, created, updated) VALUES (?,?,0,0,0,?,?)",
                                   (name, creator, now, now))
            self._db.commit()
            flow_id = cur.lastrowid
        self.add_version(flow_id, doc, by or creator, note or "first version")
        return self.get(flow_id)

    def add_version(self, flow_id: int, doc: dict, by: str = "", note: str = "", activate: bool = True) -> int:
        now = self._clock()
        with self._lock:
            number = (self._db.execute("SELECT MAX(version) FROM versions WHERE flow_id = ?", (flow_id,)).fetchone()[0] or 0) + 1
            if activate:
                self._db.execute("UPDATE versions SET status = 'retired' WHERE flow_id = ? AND status = 'active'", (flow_id,))
            self._db.execute("INSERT INTO versions (flow_id, version, status, doc, note, by, created) VALUES (?,?,?,?,?,?,?)",
                             (flow_id, number, "active" if activate else "draft", json.dumps(doc, ensure_ascii=False), note[:200], by[:40], now))
            if activate:
                self._db.execute("UPDATE flows SET active_version = ?, name = ?, updated = ? WHERE id = ?", (number, str(doc.get("name") or "").strip(), now, flow_id))
            self._db.commit()
        return number

    def doc(self, flow_id: int, version: int | None = None) -> dict | None:
        with self._lock:
            if version is None:
                row = self._db.execute("SELECT v.doc FROM versions v JOIN flows f ON f.id = v.flow_id AND f.active_version = v.version WHERE f.id = ?", (flow_id,)).fetchone()
            else:
                row = self._db.execute("SELECT doc FROM versions WHERE flow_id = ? AND version = ?", (flow_id, int(version))).fetchone()
        return _load(row[0]) if row else None

    def versions(self, flow_id: int) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT version, status, note, by, created FROM versions WHERE flow_id = ? ORDER BY version", (flow_id,)).fetchall()
        return [{"version": r[0], "status": r[1], "note": r[2], "by": r[3], "created": r[4]} for r in rows]

    def activate(self, flow_id: int, version: int) -> bool:
        with self._lock:
            row = self._db.execute("SELECT doc FROM versions WHERE flow_id = ? AND version = ?", (flow_id, int(version))).fetchone()
            if row is None:
                return False
            self._db.execute("UPDATE versions SET status = 'retired' WHERE flow_id = ? AND status = 'active'", (flow_id,))
            self._db.execute("UPDATE versions SET status = 'active' WHERE flow_id = ? AND version = ?", (flow_id, int(version)))
            self._db.execute("UPDATE flows SET active_version = ?, name = ?, updated = ? WHERE id = ?",
                             (int(version), str((_load(row[0]) or {}).get("name") or "").strip(), self._clock(), flow_id))
            self._db.commit()
        return True

    def restore(self, flow_id: int, version: int, by: str = "") -> int | None:
        """Going back is a new version that copies the old one, so the history keeps everything."""
        doc = self.doc(flow_id, version)
        return None if doc is None else self.add_version(flow_id, doc, by, f"restored version {int(version)}")

    def set_enabled(self, flow_id: int, on: bool) -> None:
        with self._lock:
            self._db.execute("UPDATE flows SET enabled = ?, updated = ? WHERE id = ?", (1 if on else 0, self._clock(), flow_id))
            self._db.commit()

    def set_archived(self, flow_id: int, archived: bool) -> None:
        with self._lock:
            self._db.execute("UPDATE flows SET archived = ?, enabled = CASE WHEN ? = 1 THEN 0 ELSE enabled END, updated = ? WHERE id = ?",
                             (1 if archived else 0, 1 if archived else 0, self._clock(), flow_id))
            self._db.commit()

    # ---- runs ----------------------------------------------------------------------------------------------------
    def start_run(self, flow_id: int, version: int, mode: str, trigger: str, input_, key: str = "", job_id: int | None = None) -> int | None:
        """None when this trigger event (its key) already started a run: the same event never starts the same work twice."""
        with self._lock:
            try:
                cur = self._db.execute("INSERT INTO runs (flow_id, version, mode, trigger, trigger_key, status, started, input, job_id) VALUES (?,?,?,?,?,'running',?,?,?)",
                                       (flow_id, int(version), mode, trigger, key, self._clock(), _dump(input_), job_id))
            except sqlite3.IntegrityError:
                return None
            self._db.commit()
            self._db.execute("DELETE FROM steps WHERE run_id IN (SELECT id FROM runs WHERE id <= ? AND status != 'running')", (cur.lastrowid - KEEP_RUNS,))
            self._db.execute("DELETE FROM runs WHERE id <= ? AND status != 'running'", (cur.lastrowid - KEEP_RUNS,))
            self._db.commit()
            return cur.lastrowid

    def finish_run(self, run_id: int, status: str, output=None, error: str = "", verified: bool = False, metrics: dict | None = None) -> None:
        with self._lock:
            self._db.execute("UPDATE runs SET status = ?, finished = ?, output = ?, error = ?, verified = ?, metrics = ? WHERE id = ?",
                             (status, self._clock(), _dump(output), error[:500], 1 if verified else 0, json.dumps(metrics or {}), run_id))
            self._db.commit()

    def set_job(self, run_id: int, job_id: int) -> None:
        with self._lock:
            self._db.execute("UPDATE runs SET job_id = ? WHERE id = ?", (job_id, run_id))
            self._db.commit()

    def reopen_run(self, run_id: int, job_id: int | None = None) -> None:
        with self._lock:
            self._db.execute("UPDATE runs SET status = 'running', finished = NULL, error = '', job_id = ? WHERE id = ?", (job_id, run_id))
            self._db.commit()

    def run(self, run_id: int) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT id, flow_id, version, mode, trigger, status, started, finished, input, output, error, verified, metrics, job_id FROM runs WHERE id = ?", (int(run_id),)).fetchone()
        return self._run(row) if row else None

    def _run(self, r) -> dict:
        return {"id": r[0], "flow_id": r[1], "version": r[2], "mode": r[3], "trigger": r[4], "status": r[5], "started": r[6], "finished": r[7],
                "input": _load(r[8]), "output": _load(r[9]), "error": r[10] or "", "verified": bool(r[11]), "metrics": _load(r[12]) or {}, "job_id": r[13]}

    def runs(self, flow_id: int | None = None, limit: int = 20) -> list[dict]:
        with self._lock:
            if flow_id is None:
                rows = self._db.execute("SELECT id, flow_id, version, mode, trigger, status, started, finished, input, output, error, verified, metrics, job_id FROM runs ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
            else:
                rows = self._db.execute("SELECT id, flow_id, version, mode, trigger, status, started, finished, input, output, error, verified, metrics, job_id FROM runs WHERE flow_id = ? ORDER BY id DESC LIMIT ?", (flow_id, int(limit))).fetchall()
        return [self._run(r) for r in rows]

    def interrupted(self) -> list[dict]:
        """Runs that were still going when the app stopped."""
        with self._lock:
            rows = self._db.execute("SELECT id, flow_id, version, mode, trigger, status, started, finished, input, output, error, verified, metrics, job_id FROM runs WHERE status = 'running'").fetchall()
        return [self._run(r) for r in rows]

    def record_step(self, run_id: int, node_id: str, status: str, *, attempt: int = 1, started: float | None = None, finished: float | None = None,
                    input_=None, output=None, error: str = "", effect: str = "") -> None:
        ms = int((finished - started) * 1000) if started is not None and finished is not None else None
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO steps (run_id, node_id, status, attempt, started, finished, ms, input, output, error, effect) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (run_id, node_id, status, attempt, started, finished, ms, _dump(input_), _dump(output), error[:500], effect))
            self._db.commit()

    def steps(self, run_id: int) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT node_id, status, attempt, started, finished, ms, input, output, error, effect FROM steps WHERE run_id = ? ORDER BY COALESCE(started, 0), node_id", (int(run_id),)).fetchall()
        return [{"node": r[0], "status": r[1], "attempt": r[2], "started": r[3], "finished": r[4], "ms": r[5], "input": _load(r[6]), "output": _load(r[7]),
                 "error": r[8] or "", "effect": r[9] or ""} for r in rows]

    def stats(self, flow_id: int, last: int = 20) -> dict:
        """How it has been going: real runs only (a test says little about the real thing)."""
        runs = [r for r in self.runs(flow_id, last) if r["mode"] == "real" and r["status"] != "running"]
        if not runs:
            return {"runs": 0}
        times = [(r["finished"] - r["started"]) for r in runs if r["finished"]]
        per_node: dict[str, list[int]] = {}
        for r in runs:
            for s in self.steps(r["id"]):
                if s["ms"] is not None:
                    per_node.setdefault(s["node"], []).append(s["ms"])
        return {"runs": len(runs), "failed": sum(1 for r in runs if r["status"] in ("failed", "blocked")),
                "avg_s": round(sum(times) / len(times), 2) if times else 0.0,
                "slowest_nodes": sorted(({"node": n, "avg_ms": int(sum(v) / len(v))} for n, v in per_node.items()), key=lambda x: -x["avg_ms"])[:3],
                "model_calls": sum(int(r["metrics"].get("model_calls", 0)) for r in runs), "tool_calls": sum(int(r["metrics"].get("tool_calls", 0)) for r in runs)}

    def close(self) -> None:
        with self._lock:
            self._db.close()
