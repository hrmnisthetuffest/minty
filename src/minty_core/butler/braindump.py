"""The brain-dump sorter (1051): a ramble becomes proposals you confirm. Words only, no model: it splits what you
said into sentences and types each one as a to-do, a follow-up, an idea or a note. Nothing is created until you
pick numbers."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

MAX_CANDIDATES = 20
MAX_LEN = 160
_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+|\s+and also\s+|\s+and then\s+|,\s*and then\s+", re.I)
_WAITING = re.compile(r"^(?i:(?:i'?m |i am |still )?waiting (?:for|on)) (?P<who>[A-Z][\w']*(?: [A-Z][\w']*)?) "
                      r"(?i:to )?(?P<text>.+)$")
_IDEA = re.compile(r"^(?:maybe|what if|how about|idea:|i could|could i|we could)\b|\bidea\b", re.I)
_NEED = re.compile(r"^(?:i )?(?:need to|have to|got to|gotta|must|should|remember to|don'?t forget to|remind me to|"
                   r"todo:?|to do:?)\s+(?P<text>.+)$", re.I)
_VERB = re.compile(r"^(?:buy|call|email|text|finish|submit|book|fix|clean|pay|order|send|print|return|renew|cancel|"
                   r"schedule|write|read|study|revise|ask|get|pick up|water|wash|charge)\b", re.I)
_DAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
_WHEN = re.compile(rf"\s+(?P<when>(?:today|tomorrow|tonight|(?:on|by|next|this) (?:{_DAYS})|"
                   rf"(?:by|on|in) \d+ \w+|next week|every .+))\s*$", re.I)


@dataclass
class Candidate:
    n: int
    kind: str  # todo | waiting | idea | note
    text: str
    when: str = ""
    who: str = ""


def _split_when(text: str) -> tuple[str, str]:
    m = _WHEN.search(text)
    return (text[:m.start()].strip(), m.group("when").strip()) if m else (text.strip(), "")


def sort(text: str) -> list[Candidate]:
    out: list[Candidate] = []
    for piece in _SPLIT.split(str(text or "")):
        piece = " ".join((piece or "").split()).strip(" .,!;")[:MAX_LEN]
        if len(piece) < 4:
            continue
        n = len(out) + 1
        m = _WAITING.match(piece)
        if m:
            body, when = _split_when(m.group("text"))
            out.append(Candidate(n, "waiting", body, when, m.group("who")))
        elif _IDEA.search(piece):
            out.append(Candidate(n, "idea", piece))
        elif (m := _NEED.match(piece)):
            body, when = _split_when(m.group("text"))
            out.append(Candidate(n, "todo", body, when))
        elif _VERB.match(piece):
            body, when = _split_when(piece)
            out.append(Candidate(n, "todo", body, when))
        else:
            out.append(Candidate(n, "note", piece))
        if len(out) >= MAX_CANDIDATES:
            break
    return out


def apply(butler, candidates: list[Candidate], picks, *, note_sink: Callable[[str, str], object] | None) -> str:
    """Create only the picked proposals. Returns a one-line summary."""
    wanted = {int(p) for p in picks if str(p).lstrip("-").isdigit()}
    added = {"to-do": 0, "follow-up": 0, "idea or note": 0}
    skipped = 0
    for c in candidates:
        if c.n not in wanted:
            continue
        try:
            if c.kind == "todo":
                butler.add("tasks", c.text, c.when)
                added["to-do"] += 1
            elif c.kind == "waiting":
                butler.add("waiting", f"{c.who}: {c.text}", c.when, {"who": c.who})
                added["follow-up"] += 1
            elif note_sink is not None:
                note_sink(c.text[:60], c.text)
                added["idea or note"] += 1
            else:
                butler.add("ideas", c.text)
                added["idea or note"] += 1
        except Exception:  # noqa: BLE001 — one bad line must not drop the rest
            skipped += 1
    total = sum(added.values())
    parts = ", ".join(f"{n} {k}" for k, n in added.items() if n)
    return f"{total} added" + (f" ({parts})" if parts else "") + (f"; {skipped} could not be added" if skipped else "") + "."
