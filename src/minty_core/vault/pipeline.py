"""The inbox → processed pipeline (710): proposals you approve. Nothing is moved, tagged or linked on its own."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from minty_core.fsutil import atomic_write_text
from minty_core.vault.index import words
from minty_core.vault.note import Note
from minty_core.vault.search import significant
from minty_core.vault.topics import STRUCTURAL


@dataclass
class Proposal:
    note_id: str
    title: str
    folder: str
    tags: list[str] = field(default_factory=list)
    project: str = ""
    links: list[str] = field(default_factory=list)
    updated: str = ""
    status: str = "pending"  # pending | rejected | done


class InboxPipeline:
    def __init__(self, vault, state: Path, clock: Callable[[], float] = time.time):
        self.vault, self._state, self._clock = vault, Path(state), clock

    def _load(self) -> dict[str, dict]:
        try:
            return json.loads(self._state.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        atomic_write_text(self._state, json.dumps(data))

    # ---- proposing -----------------------------------------------------------------------------------
    def _suggest(self, note: Note, tags_in_use, projects_in_use) -> Proposal:
        text = " " + " ".join(words(f"{note.title} {note.body}")) + " "

        def present(term: str) -> bool:
            return f" {' '.join(words(term))} " in text and bool(words(term))

        project = next((p for p in projects_in_use if present(p)), "")
        tags = [t for t in tags_in_use if t not in STRUCTURAL and present(t)][:3]
        mine = set(significant(note.title))
        links = []
        if len(mine) >= 2:
            for other in self.vault.list(None, limit=1000):
                if other.id == note.id or other.folder == "inbox" or other.confidence == "untrusted":
                    continue
                if len(mine & set(significant(other.title))) >= 2:
                    links.append(other.id)
        if note.confidence == "untrusted":
            folder = "sources"
        elif project:
            folder = "projects"
        else:
            folder = "notes"
        return Proposal(note.id, note.title, folder, tags, project, links[:2], note.updated)

    def propose(self) -> list[Proposal]:
        tags, projects = self.vault.vocabulary()
        data, new = self._load(), []
        for note in self.vault.list("inbox", limit=500):
            seen = data.get(note.id)
            if seen and seen.get("updated") == note.updated:
                continue
            p = self._suggest(note, list(tags), list(projects))
            data[note.id] = asdict(p)
            new.append(p)
        self._save(data)
        return new

    def pending(self) -> list[Proposal]:
        return [Proposal(**d) for d in self._load().values() if d.get("status") == "pending"]

    # ---- deciding ------------------------------------------------------------------------------------
    def approve(self, note_id: str) -> Note:
        data = self._load()
        entry = data.get(note_id)
        if not entry or entry.get("status") != "pending":
            raise ValueError("There is no waiting proposal for that note.")
        p = Proposal(**entry)
        note = self.vault.get(note_id)
        if note is None:
            raise ValueError("That note is gone.")
        tags = [*note.tags, *[t for t in p.tags if t not in note.tags]]
        body = note.body
        if p.links:
            related = [self.vault.get(i) for i in p.links]
            body = body.rstrip("\n") + "\n\n## Related\n" + "".join(
                f"- [{r.title}](note:{r.id})\n" for r in related if r is not None)
        self.vault.update(note_id, body=body, tags=tags, project=p.project or note.project)
        moved = self.vault.move(note_id, p.folder, approved=True)
        entry["status"] = "done"
        data[note_id] = entry
        self._save(data)
        return moved

    def reject(self, note_id: str) -> None:
        data = self._load()
        if note_id in data and data[note_id].get("status") == "pending":
            note = self.vault.get(note_id)
            data[note_id]["status"] = "rejected"
            if note is not None:
                data[note_id]["updated"] = note.updated
            self._save(data)
