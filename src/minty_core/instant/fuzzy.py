"""Spelling slips in a spoken command. Speech recognition often hears "volumn" or "set a time for 5 minutes"; the command
patterns are exact, so such a request used to fall through to the model and wait seconds for a plain timer.

A repair is deliberately narrow. Only a word of four letters or more is considered, and only when it is one edit (a letter
added, dropped, changed, or two swapped) away from exactly one command word of five letters or more; at most two words
change, and the repaired sentence must then match a command pattern *as a whole*, so it is never a guess about a loose
sentence. Short words ("skip", "mute", "next") are never targets, which keeps "ship it" from skipping a song. The words
come from the patterns handed in, so a command left out (locking the screen) can never be produced by a repair.
"""
from __future__ import annotations

import re

MIN_WORD = 4  # a word shorter than this is left alone
MIN_TARGET = 5  # a command word shorter than this is never the result of a repair
MAX_REPAIRS = 2
MAX_WORDS = 8
_GROUP = re.compile(r"\(\?P<\w+>")
_WORD = re.compile(r"[a-z]+")


def distance(a: str, b: str) -> int:
    """Edits between two words: a letter added, dropped or changed, or two neighbours swapped, each count one."""
    if a == b:
        return 0
    rows = [list(range(len(b) + 1))]
    for i in range(1, len(a) + 1):
        row = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            row[j] = min(rows[i - 1][j] + 1, row[j - 1] + 1, rows[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                row[j] = min(row[j], rows[i - 2][j - 2] + 1)
        rows.append(row)
    return rows[-1][-1]


def vocabulary_of(patterns, extra=()) -> frozenset[str]:
    """The command words in some compiled patterns (plus `extra`) that a repair may produce."""
    words = set(extra)
    for pattern in patterns:
        text = _GROUP.sub("(?:", pattern.pattern.lower())
        words.update(_WORD.findall(re.sub(r"\\[a-z]", " ", text)))
    return frozenset(w for w in words if len(w) >= MIN_TARGET)


def repair(text: str, vocab: frozenset[str]) -> str | None:
    """`text` with near-miss words put right, or None when nothing was repaired (or it is not a clear slip)."""
    words = text.split()
    if not words or len(words) > MAX_WORDS:
        return None
    out, changed = [], 0
    for word in words:
        if len(word) < MIN_WORD or word in vocab or not word.isalpha():
            out.append(word)
            continue
        near = [v for v in vocab if abs(len(v) - len(word)) <= 1 and distance(word, v) == 1]
        if len({v.rstrip("s") for v in near}) == 1:  # one word, or only its singular and plural ("minute", "minutes"): same thing
            out.append(min(near, key=lambda v: (abs(len(v) - len(word)), len(v))))
            changed += 1
        else:
            out.append(word)  # nothing close, or two command words equally close: not a clear slip
    if not changed or changed > MAX_REPAIRS:
        return None
    return " ".join(out)
