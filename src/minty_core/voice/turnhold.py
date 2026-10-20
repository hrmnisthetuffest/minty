"""Does the sentence sound unfinished? A cheap check on the transcript, so the pipeline can wait a moment for the rest
instead of answering half a thought. `unfinished` only ever *delays* a hand-off (by at most `audio.turn_hold_ms`); `looks_complete` is the
strict opposite, used to end a turn a little early when the sentence is plainly done."""
from __future__ import annotations

import re

TRAILING = {"and", "but", "or", "so", "because", "then", "with", "to", "the", "a", "an", "of", "for", "if", "that",
            "which", "like", "about", "in", "on", "at", "by", "from", "into", "than", "as", "when", "while", "where",
            "also", "my", "your", "his", "her", "their", "some"}
FILLERS = {"um", "umm", "uh", "uhh", "er", "erm", "hmm", "mm", "ah"}


_END = re.compile(r"[.?!][\"')\]\u201d\u2019]*$")


def looks_complete(text: str) -> bool:
    """Plainly finished: the recogniser closed it with a full stop, question mark or exclamation mark, and its last word is not one
    people stop on ("and", "to", "um"). Used to end a turn *early*, so it is deliberately strict; anything doubtful is not complete."""
    t = (text or "").strip()
    if not t or not _END.search(t) or t.endswith(("...", "\u2026")):
        return False
    words = re.findall(r"[a-z']+", t.lower())
    return bool(words) and words[-1] not in TRAILING and words[-1] not in FILLERS


def unfinished(text: str) -> str:
    """The reason the sentence sounds unfinished ("trailing word", "filler", "trailing pause"), or ""."""
    t = (text or "").strip().lower()
    if not t:
        return ""
    if t.endswith(("...", "…")) or t.endswith(","):
        return "trailing pause"
    words = re.findall(r"[a-z']+", t)
    if len(words) < 2:
        return ""  # a single word ("stop", "and") is judged as it is
    last = words[-1]
    if last in FILLERS:
        return "filler"
    if last in TRAILING:
        return "trailing word"
    return ""
