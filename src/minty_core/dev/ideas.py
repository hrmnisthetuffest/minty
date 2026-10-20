"""Idea and issue capture (657): a line spoken or typed becomes an entry, listed and ticked off later, exported as Markdown.
A GitHub issue is only ever a DRAFT with the exact command to approve; nothing is filed by itself."""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable

from minty_core.dev.snippets import DevDB

KINDS = ("idea", "issue", "todo")
_REPO = re.compile(r"[A-Za-z0-9._-]{1,39}/[A-Za-z0-9._-]{1,100}")


class Ideas:
    def __init__(self, path: Path | DevDB, clock: Callable[[], float] = time.time):
        self.d = path if isinstance(path, DevDB) else DevDB(path)
        self._clock = clock

    def add(self, text: str, kind: str = "idea", project: str = "") -> int:
        text = " ".join(str(text).split())[:400]
        if not text:
            raise ValueError("What is the idea?")
        if kind not in KINDS:
            raise ValueError("A note is an idea, an issue or a todo.")
        with self.d.lock:
            cur = self.d.db.execute("INSERT INTO ideas(kind, project, text, created) VALUES (?,?,?,?)",
                                    (kind, " ".join(project.split())[:60], text, self._clock()))
            self.d.db.commit()
            return cur.lastrowid

    def list(self, kind: str | None = None, project: str | None = None, status: str = "open") -> list[dict]:
        sql, args = "SELECT * FROM ideas WHERE status=?", [status]
        if kind:
            sql, args = sql + " AND kind=?", args + [kind]
        if project:
            sql, args = sql + " AND project=?", args + [project]
        with self.d.lock:
            return [dict(r) for r in self.d.db.execute(sql + " ORDER BY id", args).fetchall()]

    def done(self, ident: int) -> bool:
        with self.d.lock:
            n = self.d.db.execute("UPDATE ideas SET status='done', closed=? WHERE id=? AND status='open'",
                                  (self._clock(), ident)).rowcount
            self.d.db.commit()
        return bool(n)

    def get(self, ident: int) -> dict | None:
        with self.d.lock:
            row = self.d.db.execute("SELECT * FROM ideas WHERE id=?", (ident,)).fetchone()
        return dict(row) if row else None

    def export_markdown(self) -> str:
        rows = self.list(status="open") + self.list(status="done")
        lines = ["# Ideas and issues", ""]
        for r in sorted(rows, key=lambda r: r["id"]):
            where = f" ({r['project']})" if r["project"] else ""
            lines.append(f"- [{'x' if r['status'] == 'done' else ' '}] {r['kind']}{where}: {r['text']}")
        return "\n".join(lines) + "\n"

    def issue_draft(self, ident: int, repo: str) -> dict:
        row = self.get(ident)
        if row is None:
            raise ValueError("I have no such note.")
        if not _REPO.fullmatch(str(repo)):
            raise ValueError("Give the repository as owner/name.")
        title = row["text"][:80]
        return {"title": title, "body": row["text"], "repo": repo,
                "argv": ["gh", "issue", "create", "--repo", repo, "--title", title, "--body", row["text"]],
                "note": "This is a draft. I have not filed anything; say yes and I will run it."}
