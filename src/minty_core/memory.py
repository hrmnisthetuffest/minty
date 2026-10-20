from __future__ import annotations

import re
from pathlib import Path

from minty_core.fsutil import atomic_write_text


# passwords, PINs, one-time codes and keys are never kept in memory (it is plain text and goes into prompts)
SECRET = re.compile(r"pass(?:word|phrase|code)|\bpin\b|\botp\b|one[- ]time|\bcvv\b|api[ _-]?key|secret|token|"
                    r"\b(?:sk|gsk|ghp|xox[bp])[-_][A-Za-z0-9_-]{16,}|\b[A-Za-z0-9_-]{32,}\b", re.I)


class MemoryStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def list(self) -> list[str]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [line[2:].strip() for line in lines if line.startswith("- ")]

    def _write(self, notes: list[str]) -> None:
        atomic_write_text(self.path, "".join(f"- {n}\n" for n in notes))

    def add(self, note: str) -> None:
        note = " ".join(note.split())
        if not note:
            raise ValueError("empty note")
        self._write(self.list() + [note])

    def delete(self, index: int) -> str:
        notes = self.list()
        if not 0 <= index < len(notes):
            raise IndexError(index)
        removed = notes.pop(index)
        self._write(notes)
        return removed

    def replace(self, index: int, note: str) -> str:
        notes = self.list()
        note = " ".join(note.split())
        if not 0 <= index < len(notes):
            raise IndexError(index)
        if not note:
            raise ValueError("empty note")
        old, notes[index] = notes[index], note
        self._write(notes)
        return old

    def text(self) -> str:
        return "\n".join(f"- {n}" for n in self.list())
