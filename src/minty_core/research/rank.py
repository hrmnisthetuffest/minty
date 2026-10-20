"""Which sources are worth reading: canonical URLs, authority, relevance, recency and diversity."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from minty_core.gists import words as content_words
from minty_core.policy import registrable

_TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$|ref_|igshid|yclid|_ga|spm$)", re.I)
# (suffix or registrable domain) -> authority 0..1
_AUTHORITY = {
    "nih.gov": 0.95, "who.int": 0.92, "cdc.gov": 0.92, "nature.com": 0.9, "science.org": 0.9, "arxiv.org": 0.8,
    "wikipedia.org": 0.75, "britannica.com": 0.75, "ncbi.nlm.nih.gov": 0.95, "pubmed.ncbi.nlm.nih.gov": 0.95,
    "openalex.org": 0.8, "ieee.org": 0.88, "acm.org": 0.88, "springer.com": 0.85, "sciencedirect.com": 0.85,
    "bbc.com": 0.8, "reuters.com": 0.85, "apnews.com": 0.85, "nasa.gov": 0.92, "python.org": 0.9,
    "mozilla.org": 0.85, "kernel.org": 0.9, "github.com": 0.65, "stackoverflow.com": 0.6,
}
_FARMS = ("pinterest.", "quora.", "ehow.", "answers.com", "buzzfeed.", "listverse.", "ranker.com", "brainly.")
_TLD_AUTHORITY = {"gov": 0.9, "edu": 0.85, "ac.in": 0.8, "gov.in": 0.9, "ac.uk": 0.85, "gov.uk": 0.9, "int": 0.85}


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not _TRACKING.match(k)])
    path = parts.path.rstrip("/") or "/"
    scheme = "https" if parts.scheme in ("http", "https") else parts.scheme
    return urlunsplit((scheme, host + (f":{parts.port}" if parts.port and parts.port not in (80, 443) else ""),
                       path, query, ""))


def authority(url: str, deny: set[str] = frozenset(), allow: set[str] = frozenset()) -> float:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    dom = registrable(host)
    if dom in deny or host in deny:
        return 0.0
    if dom in allow or host in allow:
        return 0.95
    for key, value in _AUTHORITY.items():
        if host == key or host.endswith("." + key):
            return value
    for tld, value in _TLD_AUTHORITY.items():
        if host.endswith("." + tld):
            return value
    if any(f in host for f in _FARMS):
        return 0.2
    return 0.5


@dataclass
class Hit:
    url: str
    title: str
    snippet: str = ""
    kind: str = "web"  # web | academic | reference | news
    published: str = ""  # YYYY or YYYY-MM-DD
    provider: str = ""
    score: float = 0.0
    weight: float = 1.0  # learned: lowered when a claim from this domain was disputed
    extra: dict = field(default_factory=dict)


def relevance(hit: Hit, query: str) -> float:
    q = set(content_words(query))
    if not q:
        return 0.3
    have = set(content_words(f"{hit.title} {hit.snippet}"))
    return min(1.0, 0.15 + len(q & have) / len(q))


def recency(hit: Hit, today: date) -> float:
    year = int(hit.published[:4]) if re.match(r"\d{4}", hit.published or "") else None
    if year is None:
        return 0.8
    age = max(0.0, today.year - year + (0.0 if len(hit.published) < 10 else 0.0))
    half = {"news": 0.5, "web": 4.0, "reference": 10.0, "academic": 10.0}.get(hit.kind, 4.0)
    return 0.4 + 0.6 * math.pow(0.5, age / half)


def select(hits: list[Hit], n: int, query: str, *, today: date | None = None, deny: set[str] = frozenset(),
           allow: set[str] = frozenset(), weights: dict[str, float] | None = None) -> list[Hit]:
    """Up to n distinct, good sources. Greedy: each pick lowers the next from the same domain and kind, so 15 means
    15 different places, not 15 pages of one site."""
    today = today or date.today()
    seen: set[str] = set()
    pool: list[Hit] = []
    for h in hits:
        key = canonical_url(h.url)
        if key in seen or not key.startswith("http"):
            continue
        seen.add(key)
        h.weight = (weights or {}).get(registrable(urlsplit(key).hostname or ""), 1.0)
        auth = authority(key, deny, allow)
        if auth <= 0:
            continue
        h.score = relevance(h, query) * auth * recency(h, today) * h.weight
        pool.append(h)
    # content farms only fill a place when nothing better is left to pick
    farms = [h for h in pool if authority(h.url, deny, allow) < 0.3]
    pool = [h for h in pool if h not in farms]
    chosen: list[Hit] = []
    per_domain: dict[str, int] = {}
    per_kind: dict[str, int] = {}
    while pool and len(chosen) < n:
        def adjusted(h: Hit) -> float:
            dom = registrable(urlsplit(h.url).hostname or "")
            return h.score * (0.6 ** per_domain.get(dom, 0)) * (0.9 ** per_kind.get(h.kind, 0))
        best = max(pool, key=adjusted)
        pool.remove(best)
        if adjusted(best) < 0.02:
            break
        chosen.append(best)
        dom = registrable(urlsplit(best.url).hostname or "")
        per_domain[dom] = per_domain.get(dom, 0) + 1
        per_kind[best.kind] = per_kind.get(best.kind, 0) + 1
    chosen += sorted(farms, key=lambda h: -h.score)[: max(0, n - len(chosen))]
    return chosen
