"""Inline search (739): type `@your_bot shoe size` in any chat and pick one of your own notes or remembered facts.

Only the paired owner is answered; a private day or privacy mode answers with nothing. Needs inline mode switched on
for the bot in @BotFather (/setinline). The results are text from your journal and memory index, never a file.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Awaitable, Callable

from minty_core.telegram.api import TelegramError

log = logging.getLogger(__name__)

MIN_QUERY = 2
MAX_RESULTS = 8


class InlineSearch:
    def __init__(self, gateway, host_call: Callable[[str, dict], Awaitable[str]], gists=None):
        self.gw, self._call, self._gists = gateway, host_call, gists

    def install(self) -> None:
        self.gw.inline_handler = self.on_query

    async def _hits(self, query: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if self._gists is not None:
            for g in self._gists.search(query, limit=MAX_RESULTS):
                out.append((g.title or g.category or "memory", g.gist))
        try:
            lines = await self._call("journal_search_entries", {"query": query})
        except Exception:  # noqa: BLE001 — a missing journal plugin is just no journal hits
            lines = ""
        for line in str(lines).splitlines():
            if ": " in line and not line.startswith("No matches"):
                day, _, text = line.partition(": ")
                out.append((f"{day} · {text}", text))
        return out[:MAX_RESULTS]

    async def on_query(self, query: dict) -> None:
        sender = (query.get("from") or {}).get("id")
        if sender is None or sender not in self.gw.allowed:
            return  # nobody else is answered
        text = str(query.get("query") or "").strip()
        results: list[dict] = []
        if len(text) >= MIN_QUERY and not self.gw._private_now():
            for title, body in await self._hits(text):
                body = body.strip()
                if not body:
                    continue
                key = hashlib.sha1(f"{title}\n{body}".encode()).hexdigest()[:24]
                if any(r["id"] == key for r in results):
                    continue
                results.append({"type": "article", "id": key, "title": title[:64], "description": body[:120],
                                "input_message_content": {"message_text": body[:4000]}})
        try:
            await self.gw.api.answer_inline_query(str(query.get("id")), results)
        except TelegramError as e:
            log.warning("inline answer failed: %s", e)
