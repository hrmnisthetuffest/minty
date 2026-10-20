"""The decision journal (114, 701) and the weighted pros/cons helper (768).

The helper only scores what you tell it; it never picks for you. Verdicts and decisions are notes in decisions/."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from minty_core.vault.note import Note

_REVISIT = "revisit:"
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class Option:
    name: str
    score: int
    pros: list[tuple[str, int]] = field(default_factory=list)
    cons: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class Verdict:
    ranked: list[Option]
    tie: bool
    leader: str
    summary: str


def _weight(value) -> int:
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 3


def weigh(options: list[dict]) -> Verdict:
    scored: list[Option] = []
    for raw in options[:8]:
        name = " ".join(str(raw.get("name") or "").split())[:60]
        if not name:
            continue
        pros = [(" ".join(str(p.get("text") or "").split())[:80], _weight(p.get("weight", 3)))
                for p in (raw.get("pros") or [])[:10]]
        cons = [(" ".join(str(c.get("text") or "").split())[:80], _weight(c.get("weight", 3)))
                for c in (raw.get("cons") or [])[:10]]
        scored.append(Option(name, sum(w for _, w in pros) - sum(w for _, w in cons), pros, cons))
    scored.sort(key=lambda o: -o.score)  # stable: ties keep the order you gave
    if not scored:
        return Verdict([], False, "", "There is nothing to compare yet.")
    tie = len(scored) > 1 and scored[0].score == scored[1].score
    leader = "" if tie else scored[0].name
    line = ", ".join(f"{o.name} {o.score:+d}" for o in scored)
    if len(scored) == 1:
        summary = f"{scored[0].name} scores {scored[0].score:+d}."
    elif tie:
        summary = f"It is a tie at the top: {line}."
    else:
        summary = f"{leader} comes out ahead: {line}."
    return Verdict(scored, tie, leader, summary)


class Decisions:
    def __init__(self, vault):
        self.vault = vault

    def log(self, title: str, *, decision: str, why: str = "", options=(), revisit: str | None = None, tags=(),
            origin: str = "user") -> Note:
        lines = ["## Decision", decision.strip() or title, ""]
        if why.strip():
            lines += ["## Why", why.strip(), ""]
        opts = [" ".join(str(o).split()) for o in options if str(o).strip()]
        if opts:
            lines += ["## Options considered", *[f"- {o}" for o in opts], ""]
        extra = []
        if revisit and _DAY.match(revisit.strip()):
            try:
                date.fromisoformat(revisit.strip())
                extra = [f"{_REVISIT}{revisit.strip()}"]
                lines += ["## Revisit", revisit.strip(), ""]
            except ValueError:
                pass
        return self.vault.create(title, "\n".join(lines), folder="decisions", origin=origin,
                                 tags=["decision", *tags, *extra], source="decision journal")

    def list(self, limit: int = 20) -> list[Note]:
        return self.vault.list("decisions", limit=limit)

    def due(self, today: date) -> list[Note]:
        out = []
        for n in self.vault.list("decisions", limit=500):
            for t in n.tags:
                if t.startswith(_REVISIT):
                    try:
                        if date.fromisoformat(t[len(_REVISIT):]) <= today:
                            out.append(n)
                    except ValueError:
                        pass
        return out

    def save_verdict(self, question: str, verdict: Verdict, *, origin: str = "user") -> Note:
        lines = ["## Question", question.strip(), "", "## Scores"]
        for o in verdict.ranked:
            lines.append(f"- {o.name}: {o.score:+d}")
            lines += [f"  - for: {t} ({w})" for t, w in o.pros]
            lines += [f"  - against: {t} ({w})" for t, w in o.cons]
        lines += ["", verdict.summary, "", "You decide: this only adds up what you told me."]
        return self.vault.create(question, "\n".join(lines), folder="decisions", origin=origin,
                                 tags=["decision", "weighed"], source="decision helper")
