"""The daily note composer (711): a managed block with the day's plan, captures, decisions, what got done and
journal lines. Your own text outside the block is never touched. No health or finance text goes in here."""
from __future__ import annotations

import time
from datetime import date, datetime
from typing import Callable

from minty_core.vault.note import Note
from minty_core.vault.search import _OUTSIDE

BEGIN, END = "<!-- minty:begin -->", "<!-- minty:end -->"


class DailyNote:
    def __init__(self, vault, clock: Callable[[], float] = time.time):
        self.vault, self._clock = vault, clock

    @staticmethod
    def _outside_label(note: Note) -> str:
        who = next((label for key, label in _OUTSIDE if note.source.lower().startswith(key)), "from outside")
        return f"item {who} (untrusted)"

    def _block(self, day: date, plan, done, journal) -> str:
        stamp = day.isoformat()
        made = [n for n in self.vault.list(None, limit=1000)
                if n.created[:10] == stamp and n.folder not in ("daily", "decisions")]
        decisions = [n for n in self.vault.list("decisions", limit=200) if n.created[:10] == stamp]
        lines = [BEGIN]
        if plan:
            lines += ["## Plan", *[f"- {p}" for p in plan], ""]
        if made:
            lines += ["## Captured today"]
            for n in made:
                label = self._outside_label(n) if n.confidence == "untrusted" else n.title
                lines.append(f"- [{label}](note:{n.id})")
            lines.append("")
        if decisions:
            lines += ["## Decisions", *[f"- [{n.title}](note:{n.id})" for n in decisions], ""]
        if done:
            lines += ["## Done", *[f"- {d}" for d in done], ""]
        if journal:
            lines += ["## Journal", *[f"- {j}" for j in journal], ""]
        lines.append(END)
        return "\n".join(lines)

    def compose(self, day: date, plan=(), done=(), journal=()) -> Note:
        block = self._block(day, list(plan), list(done), list(journal))
        ident = f"daily-{day.isoformat()}"
        existing = self.vault.get(ident)
        if existing is None:
            return self.vault.create(f"Daily note {day.isoformat()}", block + "\n", folder="daily", origin="agent",
                                     note_id=ident, filename=day.isoformat(), source="daily composer")
        body = existing.body
        if BEGIN in body and END in body:
            head, _, rest = body.partition(BEGIN)
            _, _, tail = rest.partition(END)
            body = head + block + tail
        else:
            body = block + "\n\n" + body
        return self.vault.update(ident, body=body, origin="agent")
