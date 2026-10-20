"""The memory audit (605): everything remembered about you, where it came from, when, and a way to fix or drop it."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from minty_core.gists import source_label
from minty_core.memory import SECRET

MAX_FACT = 300


@dataclass
class Entry:
    id: str  # line:N (memory file) | fact:N / gist:N (memory index) | note:<id> (a note I wrote)
    store: str
    text: str
    source: str
    date: str = ""
    confidence: str = "told by you"
    private: bool = False
    ts: float = 0.0


class MemoryAudit:
    def __init__(self, memory, gists, vault_provider=None, clock: Callable[[], float] = time.time):
        self.memory, self.gists, self.vault, self._clock = memory, gists, vault_provider, clock

    # ---- reading -------------------------------------------------------------------------------------
    def entries(self, kind: str = "facts", query: str = "", limit: int = 200) -> list[Entry]:
        out: list[Entry] = []
        for i, line in enumerate(self.memory.list()):
            out.append(Entry(f"line:{i}", "memory file", line, "you asked me to remember"))
        if self.gists is not None:
            kinds = ("fact",) if kind == "facts" else None
            for g in self._all_gists(kinds):
                out.append(Entry(f"{'fact' if g.kind == 'fact' else 'gist'}:{g.id}", "memory index", g.gist,
                                 source_label(g.kind), datetime.fromtimestamp(g.created).strftime("%Y-%m-%d"),
                                 "told by you" if g.kind == "fact" else "inferred from conversations", g.private,
                                 g.created))
        out += self._notes()
        needle = query.strip().lower()
        if needle:
            out = [e for e in out if needle in e.text.lower()]
        out.sort(key=lambda e: -e.ts)
        return out[:limit]

    def _all_gists(self, kinds):
        db = self.gists._db  # a plain read of the table, so a correction never depends on the search index
        sql = "SELECT id FROM items" + (" WHERE kind IN (%s)" % ",".join("?" for _ in kinds) if kinds else "")
        with self.gists._lock:
            ids = [r[0] for r in db.execute(sql + " ORDER BY created DESC LIMIT 1000", list(kinds or ())).fetchall()]
        return [g for g in (self.gists.get(i) for i in ids) if g is not None]

    def _notes(self) -> list[Entry]:
        if self.vault is None or not self.vault.active():
            return []
        try:
            vault = self.vault.kit().vault
        except Exception:  # noqa: BLE001
            return []
        out = []
        for n in vault.list(None, limit=500):
            if n.confidence == "inferred" and n.folder in ("notes", "areas", "projects", "people"):
                try:
                    ts = datetime.fromisoformat(n.updated or n.created).timestamp()
                except ValueError:
                    ts = 0.0
                out.append(Entry(f"note:{n.id}", "notes folder", n.title, "notes I wrote",
                                 (n.updated or n.created)[:10], "inferred", False, ts))
        return out

    # ---- changing ------------------------------------------------------------------------------------
    @staticmethod
    def _parse(ident: str) -> tuple[str, str]:
        kind, _, rest = str(ident or "").partition(":")
        if kind not in ("line", "fact", "gist", "note") or not rest or not re.fullmatch(r"[\w\-]+", rest):
            raise ValueError("That is not one of the numbers from the memory list.")
        return kind, rest

    def edit(self, ident: str, text: str) -> Entry:
        kind, rest = self._parse(ident)
        text = " ".join(str(text).split())
        if not text or len(text) > MAX_FACT:
            raise ValueError(f"Say what it should be instead, in under {MAX_FACT} characters.")
        if SECRET.search(text):
            raise ValueError("I won't keep passwords, PINs or keys.")
        if kind == "line":
            if not rest.isdigit():
                raise ValueError("That is not one of the numbers from the memory list.")
            try:
                self.memory.replace(int(rest), text)
            except IndexError:
                raise ValueError("There is no such line in memory.") from None
            return Entry(ident, "memory file", text, "you asked me to remember")
        if kind in ("fact", "gist"):
            if not rest.isdigit() or self.gists is None or not self.gists.update(int(rest), text):
                raise ValueError("There is no such entry in memory.")
            g = self.gists.get(int(rest))
            return Entry(ident, "memory index", g.gist, source_label(g.kind),
                         datetime.fromtimestamp(g.created).strftime("%Y-%m-%d"),
                         "told by you" if g.kind == "fact" else "inferred from conversations", g.private, g.created)
        if kind == "note":
            vault = self.vault.kit().vault if self.vault is not None and self.vault.active() else None
            note = vault.get(rest) if vault is not None else None
            if note is None:
                raise ValueError("There is no such note.")
            vault.update(rest, title=text, origin="user")
            return Entry(ident, "notes folder", text, "notes I wrote", confidence="inferred")
        raise ValueError("That is not one of the numbers from the memory list.")

    def delete(self, ident: str) -> bool:
        try:
            kind, rest = self._parse(ident)
        except ValueError:
            return False
        if kind == "line":
            try:
                self.memory.delete(int(rest))
                return True
            except (IndexError, ValueError):
                return False
        if kind in ("fact", "gist"):
            return bool(rest.isdigit() and self.gists is not None and self.gists.forget(int(rest)))
        if kind == "note" and self.vault is not None and self.vault.active():
            try:
                self.vault.kit().vault.archive(rest)  # moved aside; erasing for good is "forget" with proof
                return True
            except Exception:  # noqa: BLE001
                return False
        return False
