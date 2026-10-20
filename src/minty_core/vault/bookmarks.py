"""Bookmarks with a summary (617): notes in sources/, found by words. Only plain web addresses are kept."""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from minty_core.vault.note import Note

_TRACKING = ("utm_", "fbclid", "gclid", "mc_eid", "igshid", "ref_src")


class BookmarkError(ValueError):
    pass


def normalise(url: str) -> str:
    text = str(url or "").strip()
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise BookmarkError("I can only keep plain web addresses (http or https).")
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if not k.lower().startswith(_TRACKING)])
    host = parts.hostname.lower() + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, host, parts.path or "/", query, ""))


class Bookmarks:
    def __init__(self, vault):
        self.vault = vault

    def add(self, url: str, *, title: str = "", summary: str = "", tags=(), origin: str = "agent") -> Note:
        clean = normalise(url)
        for n in self.vault.list("sources", limit=1000):
            if "bookmark" in n.tags and clean in n.body.split("\n", 1)[0]:
                return n
        host = clean.split("/")[2]
        body = clean + "\n" + (f"\n{summary.strip()}\n" if summary.strip() else "")
        return self.vault.create(title or host, body, folder="sources", origin=origin, source=f"bookmark:{host}",
                                 tags=["bookmark", *tags])

    def find(self, query: str, limit: int = 5) -> list[Note]:
        out = []
        for ident in self.vault.search_ids(query, limit=limit * 4):
            n = self.vault.get(ident)
            if n is not None and "bookmark" in n.tags:
                out.append(n)
            if len(out) >= limit:
                break
        return out
