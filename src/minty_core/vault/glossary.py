"""Your own words (609): abbreviations and names. They go to the speech recogniser as hints and, only when a request
uses one, into the prompt as a short line. A plain text file, `term = meaning`, one per line."""
from __future__ import annotations

import re
from pathlib import Path

from minty_core.fsutil import atomic_write_text

MAX_HINT = 400
MAX_PROMPT_TERMS = 5
HEADER = "# My words: one per line, term = meaning\n"


class Glossary:
    def __init__(self, path: Path):
        self.path = Path(path)

    def entries(self) -> dict[str, str]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return {}
        out: dict[str, str] = {}
        for line in lines:
            if line.lstrip().startswith("#") or "=" not in line:
                continue
            term, _, meaning = line.partition("=")
            term, meaning = " ".join(term.split()), " ".join(meaning.split())
            if term and meaning:
                out[term] = meaning
        return out

    def add(self, term: str, meaning: str) -> None:
        term, meaning = " ".join(term.split())[:40], " ".join(meaning.split())[:120]
        if not term or not meaning:
            raise ValueError("A glossary entry needs a word and what it means.")
        entries = {k: v for k, v in self.entries().items() if k.lower() != term.lower()}
        entries[term] = meaning
        atomic_write_text(self.path, HEADER + "".join(f"{k} = {v}\n" for k, v in entries.items()))

    def stt_hint(self) -> str:
        out, size = [], 0
        for term in self.entries():
            if size + len(term) + 2 > MAX_HINT:
                break
            out.append(term)
            size += len(term) + 2
        return ", ".join(out)

    def prompt_line(self, request: str) -> str:
        used = []
        for term, meaning in self.entries().items():
            if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", request, re.IGNORECASE):
                used.append(f"{term} = {meaning}")
            if len(used) >= MAX_PROMPT_TERMS:
                break
        return f"Your words: {'; '.join(used)}." if used else ""
