"""The weekly self-report (694): what the trace files of the last seven days say went wrong, how often, and what the free tiers
were used for. Counting only: nothing is invented, a missing day is said to be missing, and an error message is reduced to a
shape (numbers, paths and addresses removed) so the same fault groups together."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from minty_core.trace import read_events

SLOW_TOTAL_MS, SLOW_FIRST_MS = 15000, 8000
BAD_OUTCOMES = {"error", "failed", "timeout"}
MIN_REPEATS = {"tool": 2, "provider": 3, "slow": 3, "outcome": 2}
_URL = re.compile(r"https?://\S+")
_PATH = re.compile(r"(?:~|\.{0,2})/[\w.~+-]+(?:/[\w.~+ -]*)*")
_DIGITS = re.compile(r"\d+")


def normalise(message: str, words: int = 12) -> str:
    text = _URL.sub("<url>", str(message or "").lower())
    text = _PATH.sub("<path>", text)
    text = _DIGITS.sub("#", text)
    return " ".join(text.split()[:words])


@dataclass
class Cluster:
    kind: str  # tool | provider | slow | outcome
    key: str
    count: int
    days: int
    example: str = ""


@dataclass
class Report:
    days: int = 7
    days_with_traces: int = 0
    turns: int = 0
    answered: int = 0
    end: date | None = None
    clusters: list[Cluster] = field(default_factory=list)
    tokens: dict[str, tuple[int, int, int]] = field(default_factory=dict)  # provider -> (calls, tokens in, tokens out)


def load_week(directory: Path, today: date, days: int = 7) -> dict[str, list[dict]]:
    week = {}
    for back in range(days):
        day = (today - timedelta(days=back)).isoformat()
        events = read_events(Path(directory), day)
        if events:
            week[day] = events
    return week


def weekly_report(week: dict[str, list[dict]], today: date, days: int = 7) -> Report:
    r = Report(days=days, days_with_traces=len(week), end=today)
    groups: dict[tuple[str, str], list] = defaultdict(list)
    tokens: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for day, events in week.items():
        for e in events:
            kind = e.get("kind")
            if kind == "turn":
                r.turns += 1
                r.answered += e.get("outcome") == "answered"
                if (e.get("total_ms") or 0) > SLOW_TOTAL_MS or (e.get("first_audio_ms") or 0) > SLOW_FIRST_MS:
                    groups[("slow", "slow turns")].append((day, ""))
                if e.get("outcome") in BAD_OUTCOMES:
                    groups[("outcome", str(e["outcome"]))].append((day, ""))
            elif kind == "llm_call":
                t = tokens[str(e.get("provider") or "?")]
                t[0] += 1
                t[1] += int(e.get("tokens_in") or 0)
                t[2] += int(e.get("tokens_out") or 0)
                if e.get("outcome") == "error":
                    groups[("provider", f"{e.get('provider')} | {e.get('error_kind') or 'error'}")].append((day, str(e.get("error") or "")))
            elif kind == "tool_call" and e.get("ok") is False:
                groups[("tool", f"{e.get('name')} | {normalise(e.get('error'))}")].append((day, str(e.get("error") or "")))
    for (kind, key), hits in groups.items():
        if len(hits) >= MIN_REPEATS[kind]:
            r.clusters.append(Cluster(kind, key, len(hits), len({d for d, _ in hits}), " ".join(hits[0][1].split())[:120]))
    r.clusters.sort(key=lambda c: (-c.count, c.kind, c.key))
    r.tokens = {p: tuple(v) for p, v in tokens.items()}
    return r


def format_report(r: Report) -> str:
    if r.days_with_traces == 0:
        return f"No traces for the last {r.days} days, so there is no report to make."
    pct = round(100 * r.answered / r.turns) if r.turns else 0
    lines = [f"Week to {r.end:%-d %b} ({r.days_with_traces} of {r.days} days have traces): {r.turns} turns, {pct}% answered."]
    if not r.clusters:
        lines.append("No repeated problems this week.")
    for n, c in enumerate(r.clusters[:4], 1):
        name, _, shape = c.key.partition(" | ")
        if c.kind == "tool":
            lines.append(f"{n}. {name} failed {c.count} times the same way ({shape}).")
        elif c.kind == "provider":
            lines.append(f"{n}. {name} had {c.count} failures ({shape}) over {c.days} days.")
        elif c.kind == "slow":
            lines.append(f"{n}. {c.count} slow turns (over {SLOW_TOTAL_MS // 1000} s in all, or {SLOW_FIRST_MS // 1000} s before the first word).")
        else:
            lines.append(f"{n}. {c.count} turns ended in {name}.")
    if r.tokens:
        top = sorted(r.tokens.items(), key=lambda kv: -(kv[1][1] + kv[1][2]))[:3]
        lines.append("Tokens used: " + "; ".join(f"{p} {(i + o) // 1000}k over {c} calls" for p, (c, i, o) in top) + ".")
    return "\n".join(lines)
