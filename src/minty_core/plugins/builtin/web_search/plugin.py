import html
import re
from urllib.parse import parse_qs, urlparse

import httpx

from minty_core.httpclient import get_client
from minty_core.plugins.host import Tool
from minty_core.web.search import WIKIPEDIA, is_challenge, wikipedia_params, wikipedia_results

BRAVE = "https://api.search.brave.com/res/v1/web/search"
DDG = "https://html.duckduckgo.com/html/"
_TAGS = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    """Clean HTML from text and unescape entities. Handle None input."""
    if not text:
        return ""
    return " ".join(html.unescape(_TAGS.sub("", text)).split())


def _truncate(text: str, max_len: int = 200) -> str:
    """Truncate text to max_len characters, appending ellipsis if cut."""
    if len(text) > max_len:
        return text[:max_len] + "…"
    return text


def _unwrap(href: str) -> str:
    """Unwrap DuckDuckGo redirect URL to get actual target."""
    href = html.unescape(href)
    if href.startswith("//"):
        href = "https:" + href
    target = parse_qs(urlparse(href).query).get("uddg")
    return target[0] if target else href


def parse_ddg(page: str) -> list[dict]:
    """Parse DuckDuckGo HTML results per-anchor to avoid mis-pairing.

    Handles multi-class containers like <div class="result results_links ...">
    by splitting on each <a class="result__a"> anchor and looking for snippet
    in that anchor's block only.
    """
    results = []
    # Find all result__a anchors and their following content until the next anchor
    anchors = re.finditer(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page, re.S)
    anchor_positions = list(anchors)

    for i, anchor_match in enumerate(anchor_positions):
        href, title = anchor_match.groups()
        # Find the snippet for this result: search from this anchor to the next anchor (or end)
        block_start = anchor_match.end()
        block_end = anchor_positions[i + 1].start() if i + 1 < len(anchor_positions) else len(page)
        block = page[block_start:block_end]

        # Find the snippet within this block only
        snippet_match = re.search(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', block)
        snippet = snippet_match.group(1) if snippet_match else ""

        results.append({
            "title": _clean(title),
            "url": _unwrap(href),
            "snippet": _clean(snippet)
        })
    return results


def _format(results: list[dict]) -> str:
    """Format results into numbered lines with truncation."""
    lines = []
    for i, r in enumerate(results[:3], 1):
        title = _truncate(r['title'], 200)
        snippet = _truncate(r['snippet'], 200)
        lines.append(f"{i}. {title}: {snippet} ({urlparse(r['url']).netloc})")
    return "\n".join(lines) if lines else "I found no results."


def register(ctx):
    def helpers_on() -> bool:
        try:
            return bool(ctx.services["config"]().web.helpers)
        except Exception:  # noqa: BLE001 — no config service (a bare test host): the old behaviour
            return False

    async def wikipedia(client, query: str) -> str:
        """A source with no bot check, used only when the search engine asked for one (web.helpers)."""
        try:
            resp = await client.get(WIKIPEDIA, params=wikipedia_params(query), headers={"User-Agent": "Mozilla/5.0 (Minty)"})
            if resp.status_code != 200:
                return "Search is unavailable right now."
            rows = wikipedia_results(resp.json())
        except (httpx.HTTPError, ValueError):
            return "Search is unavailable right now."
        head = "The search engine asked for a CAPTCHA, so these come from Wikipedia."
        return f"{head} {_format(rows)}" if rows else f"{head} It has nothing on that."

    async def search(args):
        query = str(args.get("query") or "").strip()
        if not query or len(query) > 200:
            raise ValueError("query must be 1-200 characters")
        client = get_client()
        key = ctx.secret("brave_key")
        try:
            if key:
                resp = await client.get(BRAVE, params={"q": query, "count": 3},
                                        headers={"X-Subscription-Token": key, "Accept": "application/json"})
                resp.raise_for_status()
                try:
                    rows = resp.json().get("web", {}).get("results", [])
                except ValueError:
                    # Non-JSON response despite 200 status
                    return "Search is unavailable right now."
                # Use .get() for safe access and skip rows without url
                brave_results = []
                for r in rows:
                    if not r.get("url"):
                        continue
                    brave_results.append({
                        "title": _clean(r.get("title") or ""),
                        "url": r["url"],
                        "snippet": _clean(r.get("description", ""))
                    })
                return _format(brave_results)
            resp = await client.get(DDG, params={"q": query}, headers={"User-Agent": "Mozilla/5.0 (Minty)"})
            rows = parse_ddg(resp.text) if resp.status_code == 200 else []
            if helpers_on() and not rows and is_challenge(resp.status_code, resp.text):
                return await wikipedia(client, query)
            if resp.status_code != 200:
                return "Search is unavailable right now."
            return _format(rows)
        except httpx.HTTPError:
            return "Search is unavailable right now."

    schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
    return [Tool("search", "Search the web and return the top results.", schema, search, cache_ttl=3600)]
