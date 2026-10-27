"""The brain-dump sorter (1051): a ramble becomes proposals you confirm. Words only, no model: it splits what you
said into sentences and types each one as a to-do, a follow-up, an idea or a note. Nothing is created until you
pick numbers."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

MAX_CANDIDATES = 20
MAX_LEN = 160
_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+|\s+and also\s+|\s+and then\s+|,\s*and then\s+|"
                    r",\s+and\s+(?=(?:i'?m|i|we|the|my|remind me|also|then)\b)", re.I)
_LEAD = re.compile(r"^(?:(?:ok(?:ay)?|so|well|um+|uh+|like|right|oh|also|and|then|anyway|plus|yeah)\b[,\s]*)+", re.I)
_WAITING = re.compile(r"(?i:(?:\bi'?m |\bi am |\bstill )?waiting (?:for|on)) (?P<who>[A-Z][\w']*(?: [A-Z][\w']*)?|[a-z][\w']*) "
                      r"(?i:to )?(?P<text>.+)$")
_QUESTION = re.compile(r"\?$|^(?:should i|do i|can i|shall i|how |what |why |which |when should)\b", re.I)
_IDEA = re.compile(r"^(?:maybe|what if|how about|idea:|i could|could i|we could|i might|we might|i may)\b|\bidea\b|"
                   r"\bsomeday\b|\bone day\b|^i (?:think|feel|guess)\b.*\b(?:could|might)\b|\bcould (?:use|do with)\b", re.I)
_NEED = re.compile(r"^(?:i )?(?:need to|have to|got to|gotta|must|should|remember to|don'?t forget to|remind me to|"
                   r"todo:?|to do:?|promised to|promised|will|'?ll)\s+(?P<text>.+)$|^(?:i'?ll|i will)\s+(?P<text2>.+)$|"
                   r"^need to\s+(?P<text3>.+)$", re.I)
_THING_NEEDS = re.compile(r"^(?:the |my |our )?(?!(?:i|we|you|it|there|this|that)\b)[\w' -]{3,40}?\s+(?:needs|need|should|must)\s+\S", re.I)
_WHAT_IF = re.compile(r"^(?:what if|how about)\b", re.I)
_FIRM = re.compile(r"^(?:i )?(?:need to|have to|got to|gotta|must|remember to|don'?t forget to|remind me to|promised|i'?ll|will)\b", re.I)
_STOP = frozenset({"the", "a", "an", "to", "my", "our", "of", "and", "for", "on", "in"})
_VERB = re.compile(r"^(?:buy|call|email|text|finish|submit|book|fix|clean|pay|order|send|print|return|renew|cancel|"
                   r"schedule|write|read|study|revise|ask|get|pick up|water|wash|charge)\b", re.I)
_DAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
_WHEN = re.compile(rf"\s+(?P<when>(?:today|tomorrow|tonight|(?:on|by|next|this) (?:{_DAYS})|"
                   rf"(?:by|on|in) \d+ \w+|next week|every .+))\s*$", re.I)


@dataclass
class Candidate:
    n: int
    kind: str  # todo | waiting | idea | question | note
    text: str
    when: str = ""
    who: str = ""


def _split_when(text: str) -> tuple[str, str]:
    m = _WHEN.search(text)
    return (text[:m.start()].strip(), m.group("when").strip()) if m else (text.strip(), "")


def sort(text: str) -> list[Candidate]:
    out: list[Candidate] = []
    for piece in _SPLIT.split(str(text or "")):
        piece = " ".join((piece or "").split()).strip(" .,!;?")[:MAX_LEN]
        piece = _LEAD.sub("", piece).strip(" .,!;?")
        if len(piece) < 4:
            continue
        n = len(out) + 1
        asked = _QUESTION.search(piece) or (text and piece + "?" in str(text))
        m = _WAITING.search(piece)
        if m:
            body, when = _split_when(m.group("text"))
            who = m.group("who")
            out.append(Candidate(n, "waiting", body, when, who[:1].upper() + who[1:]))
        elif asked and not _FIRM.match(piece) and not _WHAT_IF.match(piece):
            out.append(Candidate(n, "question", piece))
        elif _IDEA.search(piece):
            out.append(Candidate(n, "idea", piece))
        elif (m := _NEED.match(piece)):
            body, when = _split_when(m.group("text") or m.group("text2") or m.group("text3"))
            out.append(Candidate(n, "todo", body, when))
        elif _THING_NEEDS.match(piece):
            out.append(Candidate(n, "todo", piece))
        elif _VERB.match(piece):
            body, when = _split_when(piece)
            out.append(Candidate(n, "todo", body, when))
        else:
            out.append(Candidate(n, "note", piece))
        if len(out) >= MAX_CANDIDATES:
            break
    return out


def _tokens(name: str) -> frozenset[str]:
    return frozenset(w for w in re.findall(r"[a-z0-9']+", name.lower()) if w not in _STOP)


def _same(a: str, b: str) -> bool:
    """The same thing said twice: the same words, or nearly all of them, whatever the order and the punctuation."""
    x, y = _tokens(a), _tokens(b)
    return bool(x and y) and len(x & y) / len(x | y) >= 0.8


def _known(butler, domain: str, name: str) -> bool:
    return any(_same(item.name, name) for item in butler.list(domain, "open"))


def apply(butler, candidates: list[Candidate], picks, *, note_sink: Callable[[str, str], object] | None,
          source: str | None = None) -> str:
    """Create only the picked proposals, leaving out what is already on the list. Returns a one-line summary. `source`
    is the whole original ramble: it is filed once as a note so the context behind the items is never lost."""
    wanted = {int(p) for p in picks if str(p).lstrip("-").isdigit()}
    added = {"to-do": 0, "follow-up": 0, "idea or note": 0, "decision": 0}
    skipped = repeated = 0
    for c in candidates:
        if c.n not in wanted:
            continue
        try:
            if c.kind == "todo":
                if _known(butler, "tasks", c.text):
                    repeated += 1
                    continue
                butler.add("tasks", c.text, c.when)
                added["to-do"] += 1
            elif c.kind == "waiting":
                name = f"{c.who}: {c.text}"
                if _known(butler, "waiting", name):
                    repeated += 1
                    continue
                butler.add("waiting", name, c.when, {"who": c.who})
                added["follow-up"] += 1
            elif c.kind == "question":
                name = f"Decide: {c.text}"
                if _known(butler, "ideas", name):
                    repeated += 1
                    continue
                butler.add("ideas", name)
                added["decision"] += 1
            elif note_sink is not None:
                note_sink(c.text[:60], c.text)
                added["idea or note"] += 1
            else:
                if _known(butler, "ideas", c.text):
                    repeated += 1
                    continue
                butler.add("ideas", c.text)
                added["idea or note"] += 1
        except Exception:  # noqa: BLE001 — one bad line must not drop the rest
            skipped += 1
    total = sum(added.values())
    if total and source and note_sink is not None:
        try:
            note_sink("Brain dump", source)
        except Exception:  # noqa: BLE001 — losing the copy of the original must not undo the items
            pass
    parts = ", ".join(f"{n} {k}" for k, n in added.items() if n)
    return (f"{total} added" + (f" ({parts})" if parts else "") + (f"; {repeated} already on your list" if repeated else "")
            + (f"; {skipped} could not be added" if skipped else "") + ".")
