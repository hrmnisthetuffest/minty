"""Failure learning, lite (105-lite): repeated failures from the weekly report become PROPOSALS that you approve or reject. The
proposal text and any instruction it would add are written from fixed templates around a tool or provider name that passed a strict
pattern; nothing is ever copied from a log line or a tool result (those are outside text). Nothing changes until you approve, and an
approved lesson is a normal standing instruction you can read and delete on the Prompts page."""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from minty_core.fsutil import atomic_write_text
from minty_core.selfcheck.report import SLOW_FIRST_MS, SLOW_TOTAL_MS, Cluster

_NAME = re.compile(r"[a-z][a-z0-9_]{1,60}")
_WORD = re.compile(r"[a-z_]{2,20}")
RETRY_AFTER_S = 30 * 86400


@dataclass
class Proposal:
    id: int
    kind: str
    key: str
    text: str
    action: str  # instruction | note
    instruction: str
    count: int
    status: str = "open"
    created: float = 0.0
    decided: float = 0.0


class Proposals:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path, self._clock = Path(path), clock

    def _load(self) -> list[Proposal]:
        try:
            return [Proposal(**row) for row in json.loads(self.path.read_text(encoding="utf-8"))]
        except (OSError, ValueError, TypeError):
            return []

    def _save(self, rows: list[Proposal]) -> None:
        atomic_write_text(self.path, json.dumps([asdict(r) for r in rows], indent=1), mode=0o600)

    def list(self, status: str | None = None) -> list[Proposal]:
        return [p for p in self._load() if status is None or p.status == status]

    def get(self, ident: int) -> Proposal | None:
        return next((p for p in self._load() if p.id == ident), None)

    def _template(self, c: Cluster) -> tuple[str, str, str, str] | None:
        """(identity, text, action, instruction) from fixed wording, or None if the names do not look like names."""
        name, _, shape = c.key.partition(" | ")
        if c.kind == "tool" and _NAME.fullmatch(name):
            return (name, f"{name} failed {c.count} times the same way this week. Add a standing instruction to say what went wrong, "
                          "try one different approach, and only then ask you?", "instruction",
                    f"If {name} fails, say in one sentence what went wrong, try one different approach, and only then ask the user.")
        if c.kind == "provider" and _NAME.fullmatch(name) and _WORD.fullmatch(shape or "error"):
            return (name, f"{name} failed {c.count} times this week ({shape}). Consider checking its key or moving it later in the "
                          "provider order (Switches and Brain pages).", "note", "")
        if c.kind == "slow":
            return ("slow", f"{c.count} turns were slow (over {SLOW_TOTAL_MS // 1000} s, or {SLOW_FIRST_MS // 1000} s before the first "
                            "word). Consider frugal mode or a faster model first.", "note", "")
        if c.kind == "outcome" and _WORD.fullmatch(name):
            return (name, f"{c.count} turns ended in {name}. Look at the Logs page around those times.", "note", "")
        return None

    def propose_from(self, clusters: list[Cluster]) -> list[Proposal]:
        rows, now, new = self._load(), self._clock(), []
        for c in clusters:
            made = self._template(c)
            if made is None:
                continue
            identity, text, action, instruction = made
            key = f"{c.kind}:{identity}"
            blocked = any(p.key == key and (p.status in ("open", "approved") or (p.status == "rejected" and now - p.decided < RETRY_AFTER_S))
                          for p in rows)
            if blocked:
                continue
            prop = Proposal(max([p.id for p in rows], default=0) + 1, c.kind, key, text, action, instruction, c.count, created=now)
            rows.append(prop)
            new.append(prop)
        if new:
            self._save(rows)
        return new

    def _decide(self, ident: int, status: str) -> Proposal:
        rows = self._load()
        prop = next((p for p in rows if p.id == ident), None)
        if prop is None:
            raise ValueError("I have no such proposal.")
        if prop.status != "open":
            raise ValueError(f"That one is already {prop.status}.")
        prop.status, prop.decided = status, self._clock()
        self._save(rows)
        return prop

    def approve(self, ident: int, apply: Callable[[str], object]) -> str:
        prop = next((p for p in self._load() if p.id == ident), None)
        if prop is not None and prop.status == "open" and prop.action == "instruction":
            apply(prop.instruction)
        prop = self._decide(ident, "approved")
        return (f"Added to your standing instructions: {prop.instruction}" if prop.action == "instruction"
                else "Noted. Nothing changed; this was for you to look at.")

    def reject(self, ident: int) -> str:
        self._decide(ident, "rejected")
        return "Dropped. I will not bring it up again for a month."
