"""A claim checker that shows evidence, not a verdict (665). It asks the research sources (Wikipedia, arXiv, OpenAlex, PubMed),
picks the sentence in each result that best matches the claim, quotes it word for word with its source, and flags numbers that
differ from the claim's. Deciding who is right is left to you; search snippets can be partial or out of date."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from minty_core.gists import words as content_words
from minty_core.research import providers as research_providers
from minty_core.research.text import sentences

LABELS = {"wikipedia": "Wikipedia", "arxiv": "arXiv", "openalex": "OpenAlex", "pubmed": "PubMed", "web": "Web"}
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUM.findall(text)}


@dataclass
class EvidenceItem:
    provider: str
    title: str
    url: str
    date: str
    quote: str
    raw_text: str
    numbers: set[str] = field(default_factory=set)
    differs: bool = False


@dataclass
class Evidence:
    claim: str
    items: list[EvidenceItem] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    asked: list[str] = field(default_factory=list)
    claim_numbers: set[str] = field(default_factory=set)


async def find_evidence(claim: str, get: Callable[[str, dict | None], Awaitable[str]],
                        providers: tuple[str, ...] = ("wikipedia", "arxiv", "openalex", "pubmed"),
                        per_provider: int = 3) -> Evidence:
    names = tuple(providers)
    if not set(names) <= set(LABELS):
        raise ValueError("Unknown source.")
    claim = " ".join(str(claim).split())
    if not claim or len(claim) > 400:
        raise ValueError("Give the claim in 1 to 400 characters.")
    want = set(content_words(claim))
    nums = numbers(claim)
    query = " ".join(sorted(want, key=lambda w: (-len(w), w))[:8]) or claim
    ev = Evidence(claim, asked=list(names), claim_numbers=nums)
    for name in names:
        try:
            hits = await getattr(research_providers, name)(get, query, per_provider)
        except Exception:  # noqa: BLE001 — a provider that is down is named, not fatal
            ev.failed.append(name)
            continue
        for hit in hits:
            text = str(hit.extra.get("abstract") or hit.snippet or "")
            best, best_score = "", 0.0
            for sent in sentences(text):
                score = len(want & set(content_words(sent))) + (1.5 if nums and numbers(sent) else 0.0)
                if score > best_score:
                    best, best_score = sent, score
            if best_score < 1:
                continue
            found = numbers(best)
            ev.items.append(EvidenceItem(name, hit.title, hit.url.replace("http://arxiv.org/", "https://arxiv.org/"), hit.published,
                                         best.strip(), text, found,
                                         bool(nums and found and not (nums & found))))
    return ev


def format_evidence(ev: Evidence) -> str:
    if not ev.items:
        asked = ", ".join(LABELS.get(p, p) for p in ev.asked if p not in ev.failed)
        out = [f"I found nothing on that in {asked or 'the sources I can reach'}."]
    else:
        out = [f'Evidence on: "{ev.claim}"']
        for n, i in enumerate(ev.items, 1):
            when = f" ({i.date})" if i.date else ""
            out.append(f'{n}. {LABELS.get(i.provider, i.provider)} — {i.title}{when}: "{i.quote}" {i.url}')
        for n, i in enumerate(ev.items, 1):
            if i.differs:
                out.append(f"Different numbers: source {n} gives {', '.join(sorted(i.numbers))} where the claim has "
                           f"{', '.join(sorted(ev.claim_numbers))} (check units and what is being counted).")
    if ev.failed:
        out.append("Could not ask: " + ", ".join(LABELS.get(p, p) for p in ev.failed) + ".")
    out.append("I show what the sources say; I do not decide who is right. Search snippets can be partial or out of date, "
               "so open the sources before relying on any of it.")
    return "\n".join(out)
