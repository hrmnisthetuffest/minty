"""Text tools for study, all local and extractive (nothing is invented): chapter summaries (581), a dictated note
turned into a structured one (585), rehearsal feedback (730) and school-notice triage (587)."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from minty_core.datewords import find_date

MAX_CHARS = 60_000
_STOP = frozenset("""a an the and or but if then so of to in on at for with about from by as is are was were be been
being it its this that these those there here which who whom what when where why how not no yes do does did have has
had can could should would will shall may might must i you he she we they them his her their our your my me us also
than into out up down over under more most some any each other such very just only own same too""".split())
_SENT = re.compile(r"(?<=[.!?])\s+")
_HEADING = re.compile(r"^(?:\d+(?:\.\d+)*[.)]?\s+)?[A-Z][^.!?]{1,70}$")
_FILLER_SOUNDS = re.compile(r"\b(?:um+|uh+|erm|er)\b[,]?\s*", re.I)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z][a-zA-Z'-]*", text.lower())


def _sentences(text: str) -> list[str]:
    out = []
    for s in _SENT.split(" ".join(text.split())):
        s = s.strip()
        if len(_words(s)) >= 5:
            out.append(s)
    return out


# ---- 581 summariser --------------------------------------------------------------------------------------
@dataclass
class Summary:
    title: str
    points: list[str]
    sections: list[tuple[str, list[str]]] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)

    def text(self) -> str:
        if not self.points:
            return "There is no text to summarise."
        lines = [self.title] if self.title else []
        if self.sections:
            for heading, sents in self.sections:
                lines.append(f"{heading}:")
                lines += [f"- {s}" for s in sents]
        else:
            lines += [f"- {p}" for p in self.points]
        if self.terms:
            lines.append("Key terms: " + ", ".join(self.terms))
        return "\n".join(lines)


def _split_sections(text: str) -> list[tuple[str, str]]:
    blocks = re.split(r"\n\s*\n", text.strip())
    sections: list[tuple[str, str]] = []
    heading, body = "", []
    for block in blocks:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if len(lines) == 1 and len(lines[0].split()) <= 8 and _HEADING.match(lines[0]) and not lines[0].endswith("."):
            if body or heading:
                sections.append((heading, " ".join(body)))
            heading, body = lines[0], []
        else:
            body.append(" ".join(lines))
    if body or heading:
        sections.append((heading, " ".join(body)))
    return [s for s in sections if s[1].strip()]


def summarise(text: str, points: int = 6) -> Summary:
    text = str(text or "")[:MAX_CHARS]
    if not text.strip():
        return Summary("", [])
    sections = _split_sections(text)
    freq = Counter(w for w in _words(text) if w not in _STOP and len(w) > 2)
    seen: set[str] = set()

    def score(sentence: str, first: bool) -> float:
        ws = [w for w in _words(sentence) if w not in _STOP]
        base = sum(freq[w] for w in ws) / (max(1, len(ws)) ** 0.7)
        return base * (1.2 if first else 1.0)

    chosen: list[tuple[str, list[str]]] = []
    per = max(1, points // max(1, len([s for s in sections if s[0]]) or 1))
    for heading, body in sections:
        sents = [s for s in _sentences(body) if s not in seen]
        ranked = sorted(range(len(sents)), key=lambda i: -score(sents[i], i == 0))[:per]
        picked = [sents[i] for i in sorted(ranked)]
        seen.update(picked)
        chosen.append((heading, picked))
    flat = [s for _h, ss in chosen for s in ss]
    if len(flat) > points:  # too many sections for the budget: keep the best-scoring ones, in text order
        keep = set(sorted(flat, key=lambda s: -score(s, False))[:points])
        chosen = [(h, [s for s in ss if s in keep]) for h, ss in chosen]
        flat = [s for s in flat if s in keep]
    named = [(h, ss) for h, ss in chosen if h and ss]
    title = next((h for h, _b in sections if h), flat[0][:60] if flat else "")
    terms = [w for w, _n in freq.most_common(8) if len(w) >= 5][:6]
    return Summary(title, flat, named if len(named) > 1 else [], terms)


# ---- 585 voice notes -> structured notes -------------------------------------------------------------------
_TODO = re.compile(r"\b(?:need to|have to|must|should|remember to|don'?t forget(?: to)?|homework|revise|"
                   r"bring|submit|finish|prepare)\b", re.I)


@dataclass
class Structured:
    title: str
    key_points: list[str]
    questions: list[str]
    todos: list[str]
    terms: list[str]
    subject: str = ""

    def markdown(self) -> str:
        lines = [f"# {self.title}"]
        if self.subject:
            lines.append(f"*{self.subject}*")
        if self.key_points:
            lines += ["", "## Key points", *[f"- {p}" for p in self.key_points]]
        if self.questions:
            lines += ["", "## Questions", *[f"- {q}" for q in self.questions]]
        if self.todos:
            lines += ["", "## To do", *[f"- [ ] {t}" for t in self.todos]]
        if self.terms:
            lines += ["", "## Terms", ", ".join(self.terms)]
        return "\n".join(lines) + "\n"


def structure_note(transcript: str, subject: str = "") -> Structured:
    clean = _FILLER_SOUNDS.sub("", " ".join(str(transcript).split()))
    clean = re.sub(r"\b(?:you know|so basically|i mean),?\s*", "", clean, flags=re.I)
    clean = re.sub(r"^(?:so|well|okay|ok)\s+", "", clean, flags=re.I)
    parts = [p.strip() for p in _SENT.split(clean) if len(_words(p)) >= 2]
    if not parts:
        raise ValueError("There is nothing in that to make a note of.")
    first = parts[0]
    title = (first[0].upper() + first[1:]).rstrip(".!?")[:80]
    questions, todos, points = [], [], []
    for p in parts:
        p = p[0].upper() + p[1:]
        if p.endswith("?"):
            questions.append(p)
        elif _TODO.search(p):
            todos.append(re.sub(r"^(?:i )?(?:need to|have to|must|should)\s+", "", p.rstrip(".!"), flags=re.I)
                         if not re.match(r"(?i)remember to", p) else re.sub(r"(?i)^remember to\s+", "", p.rstrip(".!")))
        else:
            points.append(p.rstrip("."))
    points = [p for p in points if p != title]
    term_counts = Counter(re.findall(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-z]{3,}(?: [A-Z][a-z]{3,})?)\b", clean))
    terms = [t for t, n in term_counts.most_common(6)]
    return Structured(title, points[:8], questions[:5], todos[:6], terms, subject)


# ---- 730 rehearsal ---------------------------------------------------------------------------------------------
_FILLERS = {"um": r"\bum+\b", "uh": r"\buh+\b", "er": r"\ber+m?\b", "you know": r"\byou know\b",
            "basically": r"\bbasically\b", "actually": r"\bactually\b", "literally": r"\bliterally\b",
            "i mean": r"\bi mean\b", "sort of": r"\bsort of\b", "kind of": r"\bkind of\b"}


@dataclass
class Rehearsal:
    words: int
    wpm: float | None
    pace: str
    fillers: dict[str, int]
    longest_sentence_words: int
    repeated: list[str]

    def text(self) -> str:
        parts = [f"{self.words} words"]
        if self.wpm is None:
            parts.append("no timing given, so I can't judge your pace")
        else:
            parts.append(f"{self.wpm:.0f} words a minute: {self.pace} (a good talking pace is about 110 to 160)")
        out = [", ".join(parts) + "."]
        if self.fillers:
            top = ", ".join(f"{w} x{n}" for w, n in sorted(self.fillers.items(), key=lambda kv: -kv[1])[:4])
            out.append(f"Filler words: {top}. Pause silently instead.")
        if self.longest_sentence_words >= 30:
            out.append(f"Try shorter sentences: your longest was {self.longest_sentence_words} words.")
        if self.repeated:
            out.append("Words you leaned on: " + ", ".join(self.repeated) + ".")
        if len(out) == 1 and self.wpm is not None and self.pace == "good":
            out.append("That sounds clean.")
        return " ".join(out)


def rehearse(text: str, seconds: float | None) -> Rehearsal:
    ws = _words(text)
    low = " ".join(str(text).lower().split())
    fillers = {name: len(re.findall(rx, low)) for name, rx in _FILLERS.items()}
    fillers = {k: v for k, v in fillers.items() if v}
    wpm = None if not seconds or seconds <= 0 else len(ws) / (seconds / 60.0)
    pace = "unknown" if wpm is None else "slow" if wpm < 110 else "fast" if wpm > 170 else "good"
    sentences = [s for s in re.split(r"[.!?]+", str(text)) if s.strip()]
    longest = max((len(_words(s)) for s in sentences), default=0)
    counts = Counter(w for w in ws if w not in _STOP and len(w) > 3)
    repeated = [w for w, n in counts.most_common(3) if n >= 4]
    return Rehearsal(len(ws), wpm, pace, fillers, longest, repeated)


# ---- 587 notice triage -----------------------------------------------------------------------------------------
_CATS = (("form", r"\b(?:consent|permission|form|slip|sign(?:ed)?|return the|submit)\b"),
         ("fee", r"\b(?:fee|fees|payment|dues|challan|fine|pay)\b"),
         ("exam", r"\b(?:exam|test|assessment|datesheet|date sheet|syllabus|viva|practical)\b"),
         ("holiday", r"\b(?:holiday|closed|closure|no school|vacation|off day)\b"),
         ("event", r"\b(?:annual day|sports day|ptm|parent[- ]teacher|trip|excursion|competition|fest|assembly|"
                  r"rehearsal|function)\b"))
_RANK = {"form": 0, "fee": 1, "exam": 2, "holiday": 3, "event": 4, "other": 5}
_URGENT = re.compile(r"\b(?:urgent|immediately|last date|deadline|today|tomorrow|tonight)\b", re.I)


@dataclass
class Triaged:
    notice: object
    category: str
    priority: str  # high | normal | low
    action: str
    day: date | None


def triage_notices(notices, today: date) -> list[Triaged]:
    out = []
    for n in notices:
        blob = f"{n.title}. {n.body}"
        cat = next((name for name, rx in _CATS if re.search(rx, blob, re.I)), "other")
        day = n.day or find_date(blob, today)
        near = day is not None and 0 <= (day - today).days <= 3
        urgent = bool(_URGENT.search(blob))
        if cat in ("form", "fee"):
            pri = "high" if near or urgent else "normal"
        elif cat == "exam":
            pri = "high" if day is not None and 0 <= (day - today).days <= 7 else "normal"
        elif cat == "other":
            pri = "low"
        else:
            pri = "normal"
        when = f" by {day:%d %b}" if day else (" by tomorrow" if re.search(r"tomorrow", blob, re.I) else "")
        action = {"form": f"Sign and return it{when}.", "fee": f"Pay{when or ' soon'}.",
                  "exam": f"Revise for it{(' (' + day.strftime('%d %b') + ')') if day else ''}: {n.body[:60]}".strip(),
                  "holiday": "No school then: nothing to hand in.",
                  "event": "Put it in your calendar.", "other": "Read it when you have a minute."}[cat]
        out.append(Triaged(n, cat, pri, action, day))
    out.sort(key=lambda t: ({"high": 0, "normal": 1, "low": 2}[t.priority], _RANK[t.category],
                            t.day or date.max))
    return out
