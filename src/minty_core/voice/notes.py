"""Noting mode: say "note for me" and Minty listens without the wake word, writing down every thing you say, until you say
"Minty, stop noting". The phrases are whole-utterance matches, so a note that happens to contain them is still a note.
Every note is on disk the moment it is heard (a crash or a power cut loses at most the sentence being spoken)."""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Callable

WAKE_WORDS = {"minty", "mintee", "minti", "mindy"}
START = {"note for me", "notes for me", "take notes", "take notes for me", "start noting", "start taking notes",
         "begin noting", "noting mode", "note taking mode"}
STOP = {"stop noting", "stop taking notes", "stop note taking", "end noting", "end notes", "finish noting",
        "finish taking notes"}
FILLER = {"um", "uh", "hmm", "hm", "mm", "er", "erm", "ah", "oh"}


def phrase(text: str) -> str:
    """The words of an utterance without punctuation, a leading "hey/ok Minty" or a trailing "please"."""
    words = re.sub(r"[?!.,;:'\"]", " ", text.lower()).split()
    while words and words[0] in {"hey", "ok", "okay"}:
        words = words[1:]
    if words and words[0] in WAKE_WORDS:
        words = words[1:]
    if words and words[-1] == "please":
        words = words[:-1]
    return " ".join(words)


def is_start(text: str) -> bool:
    return phrase(text) in START


def is_stop(text: str) -> bool:
    return phrase(text) in STOP


class NoteTaker:
    def __init__(self, path: Path, clock: Callable[[], datetime] = datetime.now):
        self.path, self._now = Path(path), clock
        self.active = False
        self._items: list[str] = []
        self._heading = False

    def begin(self) -> None:
        self.active, self._items, self._heading = True, [], False

    def add(self, text: str) -> str | None:
        note = " ".join(text.split())
        if not note or phrase(note) in FILLER:
            return None
        now = self._now()
        lines = ""
        if not self._heading:
            lines += f"\n## {now:%Y-%m-%d %H:%M}\n" if self.path.exists() else f"## {now:%Y-%m-%d %H:%M}\n"
            self._heading = True
        lines += f"- {now:%H:%M} {note}\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)  # notes are private
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(lines)
        self._items.append(note)
        return note

    def recent(self, n: int) -> list[str]:
        return self._items[-n:] if n > 0 else []

    def end(self) -> int:
        count, self.active = len(self._items), False
        return count
