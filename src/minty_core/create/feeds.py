"""RSS and Atom feeds into a short, de-duplicated news brief (658). Standard-library XML only (entity tricks are refused),
every item keeps its outlet and link, tracking parameters are removed, and a feed is not fetched more than every
`min_gap_s` seconds (the cached copy is used in between)."""
from __future__ import annotations

import difflib
import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Awaitable, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from minty_core.create.db import CreateDB

_TAGS = re.compile(r"<[^>]+>")
_TRACKING = ("utm_", "fbclid", "gclid", "mc_", "ref_src", "igshid")


@dataclass
class Item:
    title: str
    link: str
    source: str
    published: datetime | None = None
    summary: str = ""
    also: list[str] = field(default_factory=list)


def _text(value: str | None, limit: int = 200) -> str:
    return " ".join(html.unescape(_TAGS.sub(" ", value or "")).split())[:limit]


def clean_link(url: str) -> str:
    parts = urlsplit(str(url).strip())
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username:
        return ""
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(_TRACKING)])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _date(value: str | None) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        d = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_feed(xml_text: str, limit: int = 50) -> list[Item]:
    if re.search(r"<!DOCTYPE|<!ENTITY", str(xml_text), re.I):
        return []
    try:
        root = ET.fromstring(str(xml_text))
    except ET.ParseError:
        return []
    local = lambda el: el.tag.rsplit("}", 1)[-1]  # noqa: E731
    title = ""
    for el in root.iter():
        if local(el) == "title" and el.text and not title:
            title = _text(el.text, 80)
    items: list[Item] = []
    for el in root.iter():
        if local(el) not in ("item", "entry"):
            continue
        kids = {local(k): k for k in el}
        link = ""
        for k in el:
            if local(k) == "link":
                link = clean_link(k.attrib.get("href") or (k.text or ""))
                if link:
                    break
        name = _text(kids["title"].text if "title" in kids else "", 160)
        if not link or not name:
            continue
        def first(*names):  # an Element with no children is "falsy", so never use `or` on elements
            return next((kids[n] for n in names if n in kids), None)

        stamp = first("pubDate", "published", "updated", "date")
        body = first("description", "summary", "content")
        items.append(Item(name, link, title or urlsplit(link).hostname or "", _date(stamp.text if stamp is not None else ""),
                          _text(body.text if body is not None else "")))
    return items[:limit]


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower())


def dedupe(items: list[Item], similarity: float = 0.85) -> list[Item]:
    kept: list[Item] = []
    for item in items:
        for k in kept:
            if k.link == item.link or difflib.SequenceMatcher(None, _norm(k.title), _norm(item.title)).ratio() >= similarity:
                if item.source != k.source and item.source not in k.also:
                    k.also.append(item.source)
                break
        else:
            kept.append(Item(item.title, item.link, item.source, item.published, item.summary, list(item.also)))
    return kept


def _ago(then: datetime | None, now: datetime) -> str:
    if then is None:
        return ""
    hours = (now - then).total_seconds() / 3600
    if hours < 0.02:
        return "just now"
    return f"{int(hours * 60)} minutes ago" if hours < 1 else f"{int(hours)} hours ago" if hours < 48 else f"{int(hours // 24)} days ago"


class News:
    def __init__(self, fetch: Callable[[str], Awaitable[str]], db: CreateDB, clock: Callable[[], float] = None,
                 min_gap_s: float = 1800.0):
        import time as _time

        self._fetch, self.db, self._clock, self.min_gap = fetch, db, clock or _time.time, max(600.0, float(min_gap_s))

    async def _body(self, url: str) -> str | None:
        now = self._clock()
        with self.db.lock:
            row = self.db.db.execute("SELECT fetched, body FROM feed_cache WHERE url=?", (url,)).fetchone()
        if row and now - row["fetched"] < self.min_gap:
            return row["body"]
        try:
            body = await self._fetch(url)
        except Exception:  # noqa: BLE001 — a dead feed is named, never fatal
            return row["body"] if row else None
        with self.db.lock:
            self.db.db.execute("INSERT INTO feed_cache(url, fetched, body) VALUES (?,?,?) "
                               "ON CONFLICT(url) DO UPDATE SET fetched=excluded.fetched, body=excluded.body",
                               (url, now, body[:400_000]))
            self.db.db.commit()
        return body

    async def brief(self, feeds: list[str], per_feed: int = 4, max_items: int = 8, since_hours: int = 72,
                    now: datetime | None = None) -> str:
        if not feeds:
            return "No news feeds are set up yet. Add one with its web address."
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=since_hours)
        pools, failed = [], []
        for url in feeds:
            body = await self._body(url)
            items = parse_feed(body) if body else []
            if not items:
                failed.append(urlsplit(url).hostname or url)
                continue
            recent = [i for i in items if i.published is None or i.published >= cutoff]
            pools.append(sorted(recent, key=lambda i: i.published or cutoff, reverse=True)[:per_feed])
        merged: list[Item] = []
        for rank in range(per_feed):  # interleave: one story from each outlet in turn
            for pool in pools:
                if rank < len(pool):
                    merged.append(pool[rank])
        top = dedupe(merged)[:max_items]
        lines = []
        for n, i in enumerate(top, 1):
            outlet = i.source + (f", also {', '.join(i.also)}" if i.also else "")
            when = _ago(i.published, now)
            lines.append(f"{n}. {i.title} — {outlet}{', ' + when if when else ''}. {i.summary} {i.link}".replace("  ", " "))
        head = f"News from {len(pools)} source{'s' * (len(pools) != 1)}:" if lines else "No recent news in your feeds."
        out = [head, *lines]
        if failed:
            out.append(" ".join(f"{h} could not be read." for h in failed))
        return "\n".join(out)
