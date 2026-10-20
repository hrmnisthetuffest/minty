"""Spoken correction (510) and the vocabulary it teaches (509).

"No, I said weather in Paris" / "not Perry, Paris" / "I meant tomorrow" turn the previous request into the corrected
one, which is asked again once. Only the words you corrected are learned, at most `cap` of them, and they are passed
to the speech recogniser as hints."""
from __future__ import annotations

import difflib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

_I = re.IGNORECASE
EXPLICIT = [
    re.compile(r"^(?:no|nope|nah)\b[ ,]*(?:i said|i meant|i asked(?: for)?|it'?s|it is|i want(?:ed)?|that'?s|make it)"
               r"\s+(.+)$", _I),
    re.compile(r"^(?:that'?s|that is) not what i said[ ,]+(?:i said |i meant )?(.+)$", _I),
    re.compile(r"^i meant\s+(.+)$", _I),
]
BARE_SAID = re.compile(r"^i said\s+(.+)$", _I)
NOT_X = re.compile(r"^not\s+([^,]+?)\s*,\s*(?:i said |it'?s |i meant |but |i want )?(.+)$", _I)
EDGE_STOP = {"the", "a", "an", "to", "in", "on", "now", "please", "it", "my", "at", "for", "and", "of"}


@dataclass
class Correction:
    text: str
    wrong: str | None = None


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _merge(last: str, fixed: str) -> str:
    """The corrected request: a full restatement is used as it is; one or two words replace the closest word."""
    fixed = fixed.strip().rstrip(".?!")
    words = fixed.split()
    if not last or len(words) >= 4 or _ratio(last, fixed) >= 0.5:
        return fixed
    if len(words) == 1:
        tokens = last.split()
        best = max(range(len(tokens)), key=lambda i: _ratio(re.sub(r"\W", "", tokens[i]), fixed), default=None)
        if best is not None and _ratio(re.sub(r"\W", "", tokens[best]), fixed) >= 0.3:
            tokens[best] = fixed
            return " ".join(tokens)
    return fixed


def parse(said: str, last: str) -> Correction | None:
    t = " ".join((said or "").split()).rstrip(".!?")
    if not t:
        return None
    for rx in EXPLICIT:
        m = rx.match(t)
        if m:
            return Correction(_merge(last, m.group(1)))
    m = NOT_X.match(t)
    if m and last:
        wrong, fixed = m.group(1).strip(), m.group(2).strip().rstrip(".?!")
        if wrong.lower() in last.lower():
            return Correction(re.sub(re.escape(wrong), fixed, last, count=1, flags=_I), wrong)
        return Correction(_merge(last, fixed), wrong)
    m = BARE_SAID.match(t)
    if m and last and _ratio(last, m.group(1)) >= 0.4:  # "I said hello to him" is not a correction of anything
        return Correction(_merge(last, m.group(1)))
    return None


def drop_last_turn(history: list) -> int:
    """Remove the last question and everything after it. Returns how many messages went."""
    for i in range(len(history) - 1, -1, -1):
        if getattr(history[i], "role", "") == "user":
            n = len(history) - i
            del history[i:]
            return n
    return 0


class Vocabulary:
    def __init__(self, path: Path, cap: int = 40):
        self.path, self.cap = Path(path), cap
        try:
            self._terms: list[str] = [str(t) for t in json.loads(self.path.read_text(encoding="utf-8"))][-cap:]
        except (OSError, ValueError):
            self._terms = []

    def terms(self) -> list[str]:
        return list(self._terms)

    def hint(self) -> str:
        return ", ".join(self._terms)[:600]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._terms, f)

    def learn(self, wrong: str, right: str) -> list[str]:
        """The words that were new in the corrected sentence, added once each."""
        a, b = wrong.split(), right.split()
        added = []
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, [w.lower() for w in a],
                                                           [w.lower() for w in b]).get_opcodes():
            if tag not in ("replace", "insert"):
                continue
            block = b[j1:j2]
            while block and block[0].lower() in EDGE_STOP:
                block = block[1:]
            while block and block[-1].lower() in EDGE_STOP:
                block = block[:-1]
            term = " ".join(re.sub(r"[^\w'-]", "", w) for w in block).strip()
            if len(term) >= 3 and len(block) <= 4 and term.lower() not in (t.lower() for t in self._terms):
                self._terms.append(term)
                added.append(term)
        if added:
            self._terms = self._terms[-self.cap:]
            self._save()
        return added
