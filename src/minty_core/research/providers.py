"""Where to look: web, Wikipedia, arXiv, OpenAlex, PubMed. Each takes a `get(url, params) -> text` so tests feed
canned responses and the real one goes through the SSRF-checked fetcher."""
from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from typing import Awaitable, Callable
from urllib.parse import quote

from minty_core.plugins.builtin.web_search.plugin import parse_ddg
from minty_core.research.rank import Hit

Get = Callable[[str, dict | None], Awaitable[str]]
_TAGS = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    return " ".join(html.unescape(_TAGS.sub("", text or "")).split())


async def web(get: Get, query: str, n: int) -> list[Hit]:
    body = await get("https://html.duckduckgo.com/html/", {"q": query})
    return [Hit(r["url"], r["title"], r["snippet"], "web", "", "web") for r in parse_ddg(body)[:n] if r.get("url")]


async def wikipedia(get: Get, query: str, n: int) -> list[Hit]:
    data = json.loads(await get("https://en.wikipedia.org/w/api.php", {
        "action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": n}))
    out = []
    for r in data.get("query", {}).get("search", [])[:n]:
        title = str(r.get("title") or "")
        if title:
            out.append(Hit("https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_")), title,
                           _clean(r.get("snippet", "")), "reference", str(r.get("timestamp", ""))[:10], "wikipedia"))
    return out


async def arxiv(get: Get, query: str, n: int) -> list[Hit]:
    root = ET.fromstring(await get("https://export.arxiv.org/api/query", {
        "search_query": f"all:{query}", "max_results": n, "sortBy": "relevance"}))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in root.findall("a:entry", ns)[:n]:
        url = (e.findtext("a:id", "", ns) or "").strip()
        if url:
            out.append(Hit(url, _clean(e.findtext("a:title", "", ns)), _clean(e.findtext("a:summary", "", ns))[:600],
                           "academic", (e.findtext("a:published", "", ns) or "")[:10], "arxiv",
                           extra={"abstract": _clean(e.findtext("a:summary", "", ns))}))
    return out


def _abstract(index: dict | None) -> str:
    if not index:
        return ""
    slots: dict[int, str] = {}
    for word, positions in index.items():
        for p in positions:
            slots[p] = word
    return " ".join(slots[i] for i in sorted(slots))


async def openalex(get: Get, query: str, n: int) -> list[Hit]:
    data = json.loads(await get("https://api.openalex.org/works", {"search": query, "per-page": n}))
    out = []
    for w in data.get("results", [])[:n]:
        loc = w.get("primary_location") or {}
        url = loc.get("landing_page_url") or w.get("doi") or ""
        title = _clean(w.get("title") or w.get("display_name") or "")
        if url and title:
            abstract = _abstract(w.get("abstract_inverted_index"))
            out.append(Hit(url, title, abstract[:600], "academic", str(w.get("publication_year") or ""), "openalex",
                           extra={"abstract": abstract}))
    return out


async def pubmed(get: Get, query: str, n: int) -> list[Hit]:
    found = json.loads(await get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", {
        "db": "pubmed", "term": query, "retmode": "json", "retmax": n}))
    ids = found.get("esearchresult", {}).get("idlist", [])[:n]
    if not ids:
        return []
    summary = json.loads(await get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi", {
        "db": "pubmed", "id": ",".join(ids), "retmode": "json"}))
    out = []
    for uid in ids:
        r = summary.get("result", {}).get(uid) or {}
        if r.get("title"):
            out.append(Hit(f"https://pubmed.ncbi.nlm.nih.gov/{uid}/", _clean(r["title"]), _clean(r.get("source", "")),
                           "academic", str(r.get("pubdate", ""))[:4], "pubmed"))
    return out


PROVIDERS = {"web": web, "wikipedia": wikipedia, "arxiv": arxiv, "openalex": openalex, "pubmed": pubmed}
HEALTH = re.compile(r"\b(health|disease|symptom|medic|drug|vaccine|cancer|diabetes|therapy|clinical|nutrition|"
                    r"sleep|mental|heart|virus|infection|treatment|dose|vitamin)", re.I)
SCIENCE = re.compile(r"\b(physics|chemistry|biology|climate|quantum|algorithm|machine learning|neural|model|"
                     r"theory|study|research|energy|battery|space|astronomy|genetic|protein|ocean|cell)", re.I)


def pick_providers(topic: str, depth_name: str) -> list[str]:
    names = ["web", "wikipedia"]
    if SCIENCE.search(topic) or depth_name == "deep":
        names += ["arxiv", "openalex"]
    if HEALTH.search(topic):
        names += ["pubmed", "openalex"] if "openalex" not in names else ["pubmed"]
    return names
