"""A finished research becomes points in the second brain, connected.

The research pipeline already ends with checked claims: each has a quote that really is in the page it cites, sources that agree with it (`corroborated_by`) and clashes between claims whose numbers
differ. This turns that into knowledge the owner can see and use: one **hub** (a reference called "Research: topic"), one **point** (a finding) per claim the note itself used, the **original page
text** each point rests on with the exact quote, and the connections:

* a point is *part of* its hub, and rests on its source (and on the other sources that corroborate it): these are facts about how it was filed, so they are confirmed;
* a name the world model knows, and similar knowledge already in the brain, are linked only as *suggestions*, because the text came from the web and no page can decide what is connected;
* two points whose numbers disagree are marked **disputed** and linked as a *suggested* contradiction (a guess from the numbers, never a verified conflict).

Everything is outside content: unverified, never "yours". A claim that reads like orders to a model is left out. Several sources agreeing does not make a point verified; only the owner does that.
It reads the files the library saved (`claims.jsonl`, `sources.jsonl`), so it does not depend on the research run's own state, and it is done once per saved note.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

from minty_core.knowledge.capture import Capturer
from minty_core.provenance import looks_like_injection
from minty_core.research import claims as claimlib
from minty_core.research.engine import FINDINGS

log = logging.getLogger(__name__)

PAGE_TEXT = 100_000


def _read(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _claim(row: dict):
    try:
        return claimlib.Claim(text=str(row["text"]), quote=str(row.get("quote") or ""), source=str(row["source"]), chunk=int(row.get("chunk") or 0),
                              corroborated_by=[str(s) for s in row.get("corroborated_by") or []], disputed=bool(row.get("disputed")), flags=[str(f) for f in row.get("flags") or []])
    except (KeyError, TypeError, ValueError):
        return None


def _title(text: str, limit: int = 140) -> str:
    one = " ".join(text.split())
    return one if len(one) <= limit else one[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:- ") + "…"


def harvest(capturer: Capturer, note) -> dict:
    """File the points of one finished research. {"points": n, "skipped": True when it was already done, "hub": item id}."""
    store, ref = capturer.store, getattr(note, "ref", None)
    if ref is None:
        return {"points": 0, "skipped": False, "hub": ""}
    key = f"library:{ref.category}/{ref.slug}"
    if store.has_event(key, "harvested"):
        return {"points": 0, "skipped": True, "hub": ""}
    folder = Path(ref.path)
    sources = {str(s["id"]): s for s in _read(folder / "sources.jsonl") if "id" in s}
    claims = [c for c in (_claim(r) for r in _read(folder / "claims.jsonl")) if c is not None and c.text.strip() and c.source in sources]
    store.note_event(key, "research", "harvested", {"topic": str(note.topic)[:140]})  # once, even if filing fails part-way: it must not come back every time
    if not claims:
        return {"points": 0, "skipped": False, "hub": ""}
    clashes = claimlib.cross_check(claims)  # the pairs are not saved with the note: found again from the claims
    claims.sort(key=lambda c: (-c.support(), -float(sources[c.source].get("authority") or 0), c.source))
    top = [c for c in claims[: FINDINGS.get(str(note.depth), 8)] if not looks_like_injection(c.text) and not looks_like_injection(c.quote)]
    if not top:
        return {"points": 0, "skipped": False, "hub": ""}
    tags = ["research", str(ref.category).lower()]

    mine: dict[str, str] = {}  # the research's source id (S3) -> the stored original's id

    def original(sid: str) -> str:
        if sid not in mine:
            s = sources[sid]
            text = "\n\n".join(str(c) for c in s.get("chunks") or [])[:PAGE_TEXT] or str(s.get("abstract") or "")
            got = store.add_source("link", text, title=str(s.get("title") or s.get("url") or sid), uri=str(s.get("url") or ""), origin="outside", channel="research",
                                   meta={"domain": s.get("domain", ""), "authority": s.get("authority", 0), "read": s.get("note", ""), "research": key})
            mine[sid] = got["id"]
        return mine[sid]

    cited = sorted({c.source for c in top} | {s for c in top for s in c.corroborated_by if s in sources})
    hub = capturer.add("reference", _title(f"Research: {note.topic}"), f"{note.summary}\n\nConfidence: {note.confidence}. The full note is in the library ({ref.category}/{ref.slug}).",
                       tags=tags, sources=[(original(s), "") for s in cited], actor="research", outside=True, attrs={"research": key, "confidence": str(note.confidence), "depth": str(note.depth)})
    hub_id = hub["item"]["id"]
    item_of: dict[int, str] = {}
    together = {hub_id}  # the hub and the points already filed: they belong together through the hub, so they are not also proposed to each other
    for c in top:
        evidence = [(original(c.source), c.quote[:400])] + [(original(s), "") for s in c.corroborated_by if s in sources and s != c.source]
        got = capturer.add("finding", _title(c.text), c.text, tags=tags, sources=evidence, actor="research", outside=True, attrs={"research": key, "support": c.support(), "flags": c.flags}, skip_related=together)
        item_id = got["item"]["id"]
        together.add(item_id)
        item_of[id(c)] = item_id
        store.link(f"item:{item_id}", "part of", f"item:{hub_id}", why="found in this research", by="auto")
        if "contradicted" in c.flags:
            store.set_status(item_id, "disputed", actor="research", note="its numbers disagree with another source in the same research")
    for a, b in clashes:
        ia, ib = item_of.get(id(a)), item_of.get(id(b))
        if ia and ib and ia != ib:
            na, nb = sorted(claimlib._numbers(a.text)), sorted(claimlib._numbers(b.text))
            store.link(f"item:{ia}", "contradicts", f"item:{ib}", why=f"the research cross-check found different numbers ({', '.join(map(str, na))} vs {', '.join(map(str, nb))})",
                       status="suggested", by="auto", outside=True)
    return {"points": len(top), "skipped": False, "hub": hub_id}


def make_hook(capturer: Capturer, enabled: Callable[[], bool]) -> Callable:
    """The function the research plugin calls with each finished note. Never raises: a note that cannot be filed is still in the library."""
    def hook(note) -> dict | None:
        if not enabled():
            return None
        try:
            return harvest(capturer, note)
        except Exception:  # noqa: BLE001
            log.warning("could not file the research in the second brain", exc_info=True)
            return None
    return hook
