"""Which notes came from which sources (666 and the rest of the creation helpers). `audit()` flags a note whose source file was
removed or changed since the note was made, so a summary never quietly outlives what it summarised."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Callable

from minty_core.create.db import CreateDB


def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Sources:
    def __init__(self, db: CreateDB, clock: Callable[[], float] = time.time):
        self.db, self._clock = db, clock

    def record(self, note: str, title: str, kind: str, sources: list[tuple[str, str]]) -> None:
        with self.db.lock:
            self.db.db.execute("INSERT INTO derived(note, title, kind, created) VALUES (?,?,?,?) "
                               "ON CONFLICT(note) DO UPDATE SET title=excluded.title, kind=excluded.kind, flagged=''",
                               (note, title[:160], kind[:40], self._clock()))
            self.db.db.execute("DELETE FROM derived_sources WHERE note=?", (note,))
            self.db.db.executemany("INSERT INTO derived_sources(note, source, sha, kind) VALUES (?,?,?,?)",
                                   [(note, s, sha, "url" if s.startswith(("http://", "https://")) else "file")
                                    for s, sha in sources])
            self.db.db.commit()

    def forget(self, note: str) -> bool:
        with self.db.lock:
            n = self.db.db.execute("DELETE FROM derived WHERE note=?", (note,)).rowcount
            self.db.db.execute("DELETE FROM derived_sources WHERE note=?", (note,))
            self.db.db.commit()
        return bool(n)

    def list(self, flagged_only: bool = False) -> list[dict]:
        with self.db.lock:
            rows = [dict(r) for r in self.db.db.execute("SELECT note, title, kind, flagged FROM derived ORDER BY id").fetchall()]
        return [r for r in rows if r["flagged"]] if flagged_only else rows

    def audit(self) -> list[dict]:
        out = []
        with self.db.lock:
            notes = [dict(r) for r in self.db.db.execute("SELECT note, title FROM derived ORDER BY id").fetchall()]
        for n in notes:
            with self.db.lock:
                srcs = [dict(r) for r in self.db.db.execute("SELECT source, sha, kind FROM derived_sources WHERE note=?",
                                                            (n["note"],)).fetchall()]
            problems = []
            for s in srcs:
                if s["kind"] != "file":
                    continue
                path = Path(s["source"])
                if not path.is_file():
                    problems.append(f"source removed: {path.name}")
                elif s["sha"] and file_sha(path) != s["sha"]:
                    problems.append(f"source changed: {path.name}")
            why = "; ".join(problems)
            with self.db.lock:
                self.db.db.execute("UPDATE derived SET flagged=? WHERE note=?", (why, n["note"]))
                self.db.db.commit()
            if why:
                out.append({"note": n["note"], "title": n["title"], "why": why})
        return out
