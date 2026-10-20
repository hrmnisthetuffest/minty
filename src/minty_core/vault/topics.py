"""Tags from your own vocabulary (608) and resuming a topic with its context (743). No model call: a topic is a
project name or a tag you already use."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from minty_core.fsutil import atomic_write_text
from minty_core.vault.search import significant
from minty_core.vault.index import words

STRUCTURAL = {"bookmark", "decision", "weighed", "review", "time-sensitive"}
MAX_REFS = 200
_OPEN_ITEM = re.compile(r"^\s*[-*]\s*\[ \]\s+(.+)$", re.MULTILINE)


class Topics:
    def __init__(self, vault, gists=None, state: Path | None = None):
        self.vault, self.gists = vault, gists
        self._state = Path(state) if state else Path(vault.root) / "_minty" / "topics.json"

    # ---- 608 -----------------------------------------------------------------------------------------
    def tag(self, text: str, limit: int = 3) -> list[str]:
        tags, projects = self.vault.vocabulary()
        vocab = {*(t for t in tags if t not in STRUCTURAL), *projects}
        low = " " + " ".join(re.findall(r"\w+", text.lower())) + " "
        found = []
        for term in vocab:
            at = low.find(" " + " ".join(re.findall(r"\w+", term.lower())) + " ")
            if at >= 0 and term.strip():
                found.append((at, term))
        return [t for _, t in sorted(found)][:limit]

    def _load(self) -> dict:
        try:
            return json.loads(self._state.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def observe(self, ref: str, text: str, ts: float) -> list[str]:
        """Tag a saved conversation line; remember which tags it belongs to."""
        tags = self.tag(text)
        if tags:
            data = self._load()
            for t in tags:
                entries = [e for e in data.get(t, []) if e[0] != ref]
                entries.append([ref, ts])
                data[t] = entries[-MAX_REFS:]
            atomic_write_text(self._state, json.dumps(data))
        return tags

    def refs(self, topic: str) -> list[str]:
        entries = self._load().get(topic, [])
        return [r for r, _ in sorted(entries, key=lambda e: e[1])]

    # ---- 743 -----------------------------------------------------------------------------------------
    def resume(self, topic: str, limit: int = 4) -> str:
        terms = significant(topic) or words(topic)
        nothing = f"I have nothing saved about {topic.strip()}."
        if not terms:
            return nothing
        need = max(1, (len(terms) + 1) // 2)
        notes, decisions = [], []
        for ident in self.vault.search_ids(" ".join(terms), limit=limit * 5):
            n = self.vault.get(ident)
            if n is None or len(set(words(f"{n.title} {n.body} {' '.join(n.tags)} {n.project}")) & set(terms)) < need:
                continue
            (decisions if n.folder == "decisions" else notes).append(n)
        memories = []
        if self.gists is not None:
            seen = set()
            for ref in self.refs(topic):
                if ref.startswith("gist:") and ref[5:].isdigit():
                    found = [g for g in [self.gists.get(int(ref[5:]))] if g is not None]
                else:
                    found = self.gists.by_source(ref, limit=2)
                for g in found:
                    if g.id not in seen:
                        seen.add(g.id)
                        memories.append(g)
            for g in self.gists.search(" ".join(terms), limit=limit * 2, include_private=True):
                if g.id not in seen and len(set(words(g.gist)) & set(terms)) >= need:
                    memories.append(g)
        if not (notes or decisions or memories):
            return nothing
        out = [f"Where you left off with {topic.strip()}:"]
        for n in notes[:limit]:
            out.append(f"- Note: {n.title} (note:{n.id})")
        for n in decisions[:limit]:
            out.append(f"- Decision: {n.title} (note:{n.id})")
        todo = [m.group(1).strip() for n in notes + decisions for m in _OPEN_ITEM.finditer(n.body)][:5]
        for item in todo:
            out.append(f"- Still to do: {item}")
        for g in sorted(memories, key=lambda x: -x.created)[:limit]:
            out.append(f"- {datetime.fromtimestamp(g.created):%d %b %Y}: {g.gist}")
        return "\n".join(out)
