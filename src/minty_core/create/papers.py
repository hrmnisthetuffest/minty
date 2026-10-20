"""An arXiv topic watcher (659): you name a topic, it looks for new papers at most twice a day (arXiv asks for one request every
few seconds at most) and tells you only about ones it has not told you about. Titles and abstracts are outside text."""
from __future__ import annotations

import asyncio
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from minty_core.create.db import CreateDB

API = "https://export.arxiv.org/api/query"
_ATOM = {"a": "http://www.w3.org/2005/Atom"}
_TOPIC = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .,'+-]{1,79}")


@dataclass
class Paper:
    id: str
    title: str
    url: str
    published: str = ""
    authors: list[str] = field(default_factory=list)
    summary: str = ""


def parse_arxiv(xml_text: str) -> list[Paper]:
    if re.search(r"<!DOCTYPE|<!ENTITY", str(xml_text), re.I):
        return []
    try:
        root = ET.fromstring(str(xml_text))
    except ET.ParseError:
        return []
    out = []
    for e in root.findall("a:entry", _ATOM):
        raw = (e.findtext("a:id", "", _ATOM) or "").strip()
        m = re.search(r"abs/([A-Za-z0-9./-]+?)(?:v\d+)?$", raw)
        if not m:
            continue
        out.append(Paper(m.group(1), " ".join((e.findtext("a:title", "", _ATOM) or "").split()),
                         f"https://arxiv.org/abs/{m.group(1)}", (e.findtext("a:published", "", _ATOM) or "")[:10],
                         [(a.findtext("a:name", "", _ATOM) or "").strip() for a in e.findall("a:author", _ATOM)],
                         " ".join((e.findtext("a:summary", "", _ATOM) or "").split())[:400]))
    return out


class PaperWatch:
    def __init__(self, db: CreateDB, get: Callable[[str, dict | None], Awaitable[str]],
                 clock: Callable[[], float] = time.time, min_gap_s: float = 43200.0,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.db, self._get, self._clock, self.min_gap, self._sleep = db, get, clock, max(21600.0, float(min_gap_s)), sleep

    def add(self, topic: str) -> str:
        topic = " ".join(str(topic).split())
        if not _TOPIC.fullmatch(topic):
            raise ValueError("A topic is plain words (letters, digits and . , ' + -), up to 80 characters.")
        with self.db.lock:
            self.db.db.execute("INSERT OR IGNORE INTO topics(topic, created) VALUES (?,?)", (topic, self._clock()))
            self.db.db.commit()
        return f"Watching arXiv for '{topic}'. I will tell you about new papers, checking at most twice a day."

    def remove(self, topic: str) -> bool:
        with self.db.lock:
            n = self.db.db.execute("DELETE FROM topics WHERE topic=?", (" ".join(str(topic).split()),)).rowcount
            self.db.db.execute("DELETE FROM seen WHERE topic=?", (" ".join(str(topic).split()),))
            self.db.db.commit()
        return bool(n)

    def topics(self) -> list[str]:
        with self.db.lock:
            return [r["topic"] for r in self.db.db.execute("SELECT topic FROM topics ORDER BY created, topic").fetchall()]

    async def check(self) -> list[str]:
        now, out, asked = self._clock(), [], False
        with self.db.lock:
            rows = [dict(r) for r in self.db.db.execute("SELECT * FROM topics").fetchall()]
        for row in rows:
            if row["last_check"] is not None and now - row["last_check"] < self.min_gap:
                continue
            if asked:
                await self._sleep(3.0)
            asked = True
            try:
                body = await self._get(API, {"search_query": f"all:{row['topic']}", "max_results": 10,
                                             "sortBy": "submittedDate", "sortOrder": "descending"})
            except Exception:  # noqa: BLE001 — try again next time
                continue
            papers = parse_arxiv(body)
            with self.db.lock:
                known = {r["paper_id"] for r in self.db.db.execute("SELECT paper_id FROM seen WHERE topic=?",
                                                                    (row["topic"],)).fetchall()}
                self.db.db.executemany("INSERT OR IGNORE INTO seen(topic, paper_id) VALUES (?,?)",
                                       [(row["topic"], p.id) for p in papers])
                self.db.db.execute("UPDATE topics SET last_check=? WHERE topic=?", (now, row["topic"]))
                self.db.db.commit()
            if row["last_check"] is None:
                continue  # the first look only learns what is already there
            for p in papers:
                if p.id not in known:
                    who = ", ".join(p.authors[:3]) + (" and others" if len(p.authors) > 3 else "")
                    out.append(f"New on arXiv for '{row['topic']}': {p.title} ({who}, {p.published}) {p.url}")
        return out
