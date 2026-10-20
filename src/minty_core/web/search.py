"""Search without CAPTCHA traps (560): spot a search engine's bot check and fall back to a source that has none (the
Wikipedia search API). It never tries to solve or get around a check."""
from __future__ import annotations

import html
import re
from urllib.parse import quote

CHALLENGE = re.compile(r"anomaly-modal|bots use duckduckgo|/sorry/index|unusual traffic from your computer network|"
                       r"verify you are human|g-recaptcha|please complete the security check|are you a robot", re.I)
WIKIPEDIA = "https://en.wikipedia.org/w/api.php"
_TAGS = re.compile(r"<[^>]+>")


def is_challenge(status: int, text: str) -> bool:
    """True if a search engine answered with a bot check instead of results."""
    return status in (202, 403, 429) or bool(CHALLENGE.search(str(text or "")[:20000]))


def wikipedia_params(query: str, limit: int = 3) -> dict:
    return {"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": limit,
            "utf8": 1}


def wikipedia_results(data: dict) -> list[dict]:
    out = []
    for row in (data.get("query", {}) or {}).get("search", []) or []:
        title = str(row.get("title") or "")
        if not title:
            continue
        snippet = " ".join(html.unescape(_TAGS.sub("", str(row.get("snippet") or ""))).split())
        out.append({"title": title, "url": "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_")),
                    "snippet": snippet})
    return out
