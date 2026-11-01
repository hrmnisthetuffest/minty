"""The research pipeline: plan -> search -> select -> fetch -> extract -> cross-check -> note -> library.

One engine, three budgets. State is written after every stage (and every few sources), so a killed deep run
continues where it stopped. Nothing here decides *whether* to research: that permission is the caller's.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from minty_core.fsutil import atomic_write_text
from minty_core.policy import registrable
from minty_core.research import claims as claimlib
from minty_core.research.claims import Ask, Claim
from minty_core.research.library import Library, NoteRef
from minty_core.research.providers import PROVIDERS, pick_providers
from minty_core.research.rank import Hit, authority, select
from minty_core.research.text import chunk_text

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Depth:
    name: str
    sources: int
    subquestions: int
    chunks_per_source: int
    concurrency: int
    seconds: int
    minutes: str  # for the spoken permission question


DEPTHS = {
    "light": Depth("light", 6, 2, 3, 4, 240, "1 to 3 minutes"),
    "normal": Depth("normal", 18, 4, 5, 4, 900, "5 to 10 minutes"),
    "deep": Depth("deep", 80, 8, 6, 5, 2700, "15 to 40 minutes"),
}
FINDINGS = {"light": 8, "normal": 16, "deep": 30}
PAGE_CHARS = 14_000
PLAN_PROMPT = """You plan a web research. Given a topic, write sub-questions that look at it from different \
perspectives (basics, causes or mechanisms, evidence, criticism or limits, practical use). Reply with JSON only: \
{"subquestions": ["...", "..."]}. Short, searchable questions."""
NOTE_PROMPT = """Write a research note in Markdown from ONLY the numbered claims below. Use exactly these sections: \
"## Summary" (3 to 5 sentences), "## Key findings" (bullets; each ends with its [S#] citations), "## Where \
sources disagree" (only if claims are marked contradicted), "## Open questions". Cite only the S numbers given; \
never add a fact that is not in a claim."""
_SUFFIXES = ("overview", "how it works", "evidence and studies", "criticism and limitations", "practical advice",
             "history", "latest developments", "common misconceptions")


class ResearchError(Exception):
    pass


@dataclass
class Note:
    topic: str
    depth: str
    summary: str  # what is spoken: two or three sentences
    markdown: str
    confidence: str
    n_sources: int
    n_claims: int
    used_model: bool
    ref: NoteRef | None = None


Search = Callable[..., Awaitable[list[Hit]]]
Fetch = Callable[[str], Awaitable[tuple[str, str, str]]]  # url -> (title, text, note)


class Research:
    def __init__(self, *, ask: Ask | None, get, fetch: Fetch, library: Library, state_dir: Path,
                 progress: Callable[[str], None] = lambda t: None, today: Callable[[], date] = date.today,
                 deny: frozenset[str] | set[str] = frozenset(), allow: frozenset[str] | set[str] = frozenset(),
                 providers: dict[str, Search] | None = None, tracer=None):
        self._ask, self._get, self._fetch, self._lib = ask, get, fetch, library
        self._dir = Path(state_dir)
        self._progress, self._today, self._deny, self._allow = progress, today, set(deny), set(allow)
        self._providers = providers or PROVIDERS
        self._tracer = tracer

    # -- state ------------------------------------------------------------------------------------------
    def _path(self, run_id: str) -> Path:
        return self._dir / run_id / "state.json"

    def load(self, run_id: str) -> dict | None:
        try:
            return json.loads(self._path(run_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _save(self, run_id: str, st: dict) -> None:
        atomic_write_text(self._path(run_id), json.dumps(st, ensure_ascii=False), 0o600)

    @staticmethod
    def new_id() -> str:
        return time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]

    def unfinished(self) -> list[tuple[str, dict]]:
        out = []
        for p in sorted(self._dir.glob("*/state.json")):
            try:
                st = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if st.get("stage") != "done":
                out.append((p.parent.name, st))
        return out

    # -- the run ----------------------------------------------------------------------------------------
    async def run(self, topic: str, depth_name: str = "normal", run_id: str | None = None,
                  questions: list[str] | None = None) -> Note:
        """questions: extra sub-questions to look into besides the ones planned (a blueprint template's)."""
        depth = DEPTHS.get(depth_name) or DEPTHS["normal"]
        run_id = run_id or self.new_id()
        st = self.load(run_id) or {"topic": topic, "depth": depth.name, "stage": "plan", "queries": [], "hits": [],
                                   "sources": {}, "extracted": [], "claims": [], "used_model": False,
                                   "started": time.time(), "extra": list(questions or [])}
        topic = st["topic"]
        for stage, fn in (("plan", self._plan), ("search", self._search), ("select", self._select),
                          ("fetch", self._fetch_all), ("extract", self._extract), ("write", self._write)):
            if self._order(st["stage"]) > self._order(stage):
                continue
            st["stage"] = stage
            self._progress(f"{stage}")
            await fn(st, depth, run_id)
            self._save(run_id, st)
        st["stage"] = "done"
        self._save(run_id, st)
        data = dict(st["note"])
        data["ref"] = NoteRef(data["ref"]["category"], data["ref"]["slug"], Path(data["ref"]["path"]))
        return Note(**data)

    @staticmethod
    def _order(stage: str) -> int:
        return ["plan", "search", "select", "fetch", "extract", "write", "done"].index(stage)

    async def _model(self, system: str, user: str) -> str | None:
        if self._ask is None:
            return None
        try:
            return await self._ask(system, user)
        except Exception as e:  # noqa: BLE001 — no model: the plain way still works
            log.info("research model unavailable: %s", e)
            return None

    # -- stages -----------------------------------------------------------------------------------------
    async def _plan(self, st: dict, depth: Depth, run_id: str) -> None:
        topic = st["topic"]
        sub: list[str] = []
        reply = await self._model(PLAN_PROMPT, f"Topic: {topic}\nWrite {depth.subquestions} sub-questions.")
        if reply:
            start, end = reply.find("{"), reply.rfind("}")
            try:
                data = json.loads(reply[start:end + 1])
                sub = [str(q).strip()[:150] for q in data.get("subquestions", []) if str(q).strip()]
            except ValueError:
                sub = []
        if not sub:
            sub = [f"{topic} {s}" for s in _SUFFIXES[:depth.subquestions]]
        extra = [str(q) for q in st.get("extra", [])][:depth.subquestions]
        st["queries"] = [topic] + extra + sub[:max(0, depth.subquestions - len(extra))]

    async def _search(self, st: dict, depth: Depth, run_id: str) -> None:
        names = [n for n in pick_providers(st["topic"], depth.name) if n in self._providers]
        per_query = max(4, -(-depth.sources * 3 // max(1, len(st["queries"]) * len(names))))
        gate = asyncio.Semaphore(6)

        async def one(name: str, query: str) -> list[Hit]:
            async with gate:
                try:
                    return await self._providers[name](self._get, query, per_query)
                except Exception as e:  # noqa: BLE001 — one engine down never ends the research
                    log.info("search %s failed: %s", name, type(e).__name__)
                    return []
        batches = await asyncio.gather(*(one(n, q) for q in st["queries"] for n in names))
        seen: set[str] = set()
        hits = []
        for batch in batches:
            for h in batch:
                if h.url not in seen:
                    seen.add(h.url)
                    hits.append(asdict(h))
        st["hits"] = hits
        if not hits:
            raise ResearchError("no search engine returned anything: is the internet reachable?")

    async def _select(self, st: dict, depth: Depth, run_id: str) -> None:
        hits = [Hit(**h) for h in st["hits"]]
        chosen = select(hits, depth.sources, st["topic"], today=self._today(), deny=self._deny, allow=self._allow,
                        weights=self._lib.weights())
        if not chosen:
            raise ResearchError("every result was filtered out as unreliable")
        st["sources"] = {}
        for i, h in enumerate(chosen, 1):
            dom = registrable(urlsplit(h.url).hostname or "")
            st["sources"][f"S{i}"] = {"id": f"S{i}", "url": h.url, "title": h.title[:140], "kind": h.kind,
                                      "domain": dom, "published": h.published, "snippet": h.snippet[:300],
                                      "abstract": str(h.extra.get("abstract", ""))[:2000], "chunks": [], "note": "",
                                      "authority": authority(h.url, self._deny, self._allow)}

    async def _fetch_all(self, st: dict, depth: Depth, run_id: str) -> None:
        gate = asyncio.Semaphore(depth.concurrency)
        todo = [s for s in st["sources"].values() if not s["chunks"] and not s["note"]]
        done = 0

        async def one(s: dict) -> None:
            nonlocal done
            async with gate:
                try:
                    title, text, note = await self._fetch(s["url"])
                except Exception as e:  # noqa: BLE001
                    title, text, note = "", "", f"could not be read ({type(e).__name__})"
                text = (text or "")[:PAGE_CHARS]
                if len(text) < 200 and s["abstract"]:
                    text, note = s["abstract"], (note or "") + " abstract only"
                s["chunks"] = chunk_text(text) if len(text) >= 80 else []
                s["note"] = note or ("read" if s["chunks"] else "empty")
                if title and not s["title"]:
                    s["title"] = title[:140]
                done += 1
                self._progress(f"read {done} of {len(todo)} sources")
                if done % 5 == 0:
                    self._save(run_id, st)
        await asyncio.gather(*(one(s) for s in todo))

    async def _extract(self, st: dict, depth: Depth, run_id: str) -> None:
        gate = asyncio.Semaphore(2)  # free-tier models rate limit: few calls at once
        todo = [s for s in st["sources"].values() if s["chunks"] and s["id"] not in st["extracted"]]
        done = 0

        async def one(s: dict) -> None:
            nonlocal done
            async with gate:
                found, used = await claimlib.extract(self._ask, st["topic"], s["id"], s["chunks"],
                                                     depth.chunks_per_source)
                st["used_model"] = st["used_model"] or used
                st["claims"].extend(asdict(c) for c in found)
                st["extracted"].append(s["id"])
                done += 1
                self._progress(f"checked {done} of {len(todo)} sources")
                if done % 5 == 0:
                    self._save(run_id, st)
        await asyncio.gather(*(one(s) for s in todo))

    async def _write(self, st: dict, depth: Depth, run_id: str) -> None:
        claims = [Claim(**c) for c in st["claims"]]
        if not claims:
            raise ResearchError("none of the sources gave a claim I could check")
        clashes = claimlib.cross_check(claims)
        sources = st["sources"]
        claims.sort(key=lambda c: (-c.support(), -sources[c.source]["authority"], c.source))
        top = claims[:FINDINGS[depth.name]]
        reply = await self._model(NOTE_PROMPT, "Topic: " + st["topic"] + "\n\nClaims:\n" + "\n".join(
            f"[{c.source}] {c.text}" + (" (contradicted)" if "contradicted" in c.flags else "")
            + (f" (also {', '.join(c.corroborated_by)})" if c.corroborated_by else "") for c in top))
        body = self._body(reply, top, clashes)
        confidence = self._confidence(claims, clashes, len(sources))
        md = self._markdown(st, depth, body, sources, confidence, used_all=sorted({c.source for c in claims}))
        summary = self._spoken(st["topic"], depth, body, len(sources), len(claims), confidence, top)
        ref = self._lib.save(st["topic"], depth.name, md, [{k: v for k, v in s.items()} for s in sources.values()],
                             [{**asdict(c), "support": c.support()} for c in claims], summary, confidence)
        st["note"] = {"topic": st["topic"], "depth": depth.name, "summary": summary, "markdown": md,
                      "confidence": confidence, "n_sources": len(sources), "n_claims": len(claims),
                      "used_model": st["used_model"],
                      "ref": {"category": ref.category, "slug": ref.slug, "path": str(ref.path)}}

    # -- note assembly ----------------------------------------------------------------------------------
    @staticmethod
    def _body(reply: str | None, top: list[Claim], clashes) -> str:
        valid = {c.source for c in top} | {s for c in top for s in c.corroborated_by}
        if reply and "## Summary" in reply and "## Key findings" in reply:
            cited = set(re.findall(r"\[(S\d+)\]", reply))
            if cited & valid:  # a model that cited nothing real is not trusted: the plain note is written instead
                for s in cited - valid:  # an invented citation is removed, never kept
                    reply = reply.replace(f"[{s}]", "")
                return reply.strip()
        lines = ["## Summary",
                 f"This note collects {len(top)} checked statements from {len(valid)} sources, most supported first. "
                 "It was assembled without a model, so the wording is the sources' own.", "", "## Key findings"]
        for c in top:
            cites = "".join(f"[{s}]" for s in [c.source] + c.corroborated_by)
            lines.append(f"- {c.text} {cites}" + (" (disputed between sources)" if "contradicted" in c.flags else ""))
        if clashes:
            lines += ["", "## Where sources disagree"] + [f'- "{a.text}" [{a.source}] vs "{b.text}" [{b.source}]'
                                                         for a, b in clashes[:6]]
        lines += ["", "## Open questions", "- What would change this: newer studies, or a primary source that settles "
                  "the disagreements above."]
        return "\n".join(lines)

    @staticmethod
    def _confidence(claims: list[Claim], clashes, n_sources: int) -> str:
        backed = sum(1 for c in claims if c.support() >= 2)
        if n_sources < 3 or backed == 0:
            return "low"
        return "high" if backed >= 3 and len(clashes) <= 1 and n_sources >= 5 else "medium"

    def _markdown(self, st: dict, depth: Depth, body: str, sources: dict, confidence: str, used_all: list[str]) -> str:
        head = (f"# {st['topic']}\n*{depth.name} research · {self._today():%d %B %Y} · {len(sources)} sources · "
                f"confidence: {confidence}" + ("" if st["used_model"] else " · written without a model") + "*\n\n")
        refs = [f"{s['id']}. {s['title'] or s['url']} — {s['domain']} ({s['kind']}"
                + (f", {s['published'][:4]}" if s["published"] else "") + f") {s['url']}"
                + (f" _{s['note']}_" if s["note"] not in ("read", "") else "")
                for s in sources.values() if s["id"] in used_all]
        return head + body.strip() + "\n\n## Sources\n" + "\n".join(refs) + "\n"

    @staticmethod
    def _spoken(topic: str, depth: Depth, body: str, n_sources: int, n_claims: int, confidence: str,
                top: list[Claim]) -> str:
        m = re.search(r"## Summary\s*(.+?)(?:\n##|\Z)", body, re.S)
        first = " ".join(m.group(1).split()) if m else ""
        sentence = re.split(r"(?<=[.!?])\s+", first)[:1]
        lead = sentence[0] if sentence and sentence[0] else (top[0].text if top else "")
        return (f"Finished the {depth.name} research on {topic}: {n_sources} sources, {n_claims} checked claims, "
                f"{confidence} confidence. {lead} The full note is saved in the library.").replace("..", ".")
