"""Gmail, read only, through the Composio server you set up.

`ComposioRunner` opens one session per round and refuses to do anything unless you allowed the Composio tool it needs and its
description is the one you looked at (the same pinning as every other MCP call); a changed description switches it off until you look
again. `ComposioGmail` turns the watcher's questions into read-only steps (`composio_rules.call_class` must say class 1 for every one,
or nothing is sent) and Composio's real answers (their shapes were taken from live responses) into plain, bounded values. A query is
never built from message text: the only thing put into one is an address that passed a strict check.
"""
from __future__ import annotations

import contextlib
import json
import re
import time
from datetime import datetime, timezone
from typing import Callable

from minty_core.composio_rules import call_class
from minty_core.gmailwatch import HistoryExpired
from minty_core.mailscore import ADDRESS, Mail
from minty_core.mcp.client import open_session
from minty_core.mcp.registry import McpRegistry, tool_hash

EXECUTE = "COMPOSIO_MULTI_EXECUTE_TOOL"
SEARCH = "COMPOSIO_SEARCH_TOOLS"
KEEP_HEADERS = ("list-unsubscribe", "precedence", "auto-submitted", "authentication-results")
STRICT_ADDRESS = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){1,5}")
ACCOUNTS_FOR_S = 3600.0


class ComposioRunner:
    def __init__(self, registry: McpRegistry, secret: Callable[[str], str | None], server: str = "composio", timeout: float = 60.0,
                 needs: tuple[str, ...] = (EXECUTE,), optional: tuple[str, ...] = ()):
        self._reg, self._secret, self._server, self._timeout, self._needs, self._optional = registry, secret, server, timeout, needs, optional
        self._session = None
        self._ok: set[str] = set()

    @contextlib.asynccontextmanager
    async def round(self):
        async with open_session(self._server, self._reg.get(self._server), self._secret, timeout=self._timeout) as session:
            await session.initialize()
            live = {t["name"]: t for t in await session.list_tools()}
            self._ok = {name for name in self._needs + self._optional
                        if name in live and self._reg.status(self._server, name, tool_hash(live[name]), live[name]) == "allowed"}
            missing = [n for n in self._needs if n not in self._ok]
            if missing:
                raise PermissionError(f"{', '.join(missing)} is not allowed on {self._server} (or its description changed since you allowed it): "
                                      f"minty mcp tools {self._server}, then minty mcp allow {self._server} {missing[0]}")
            self._session = session
            try:
                yield self
            finally:
                self._session, self._ok = None, set()

    async def call(self, tool: str, arguments: dict, max_chars: int = 500_000) -> dict:
        if self._session is None or tool not in self._ok:
            raise PermissionError(f"{tool} may not be called here")
        result = await self._session.call_tool(tool, arguments, max_chars=max_chars)
        try:
            data = json.loads(result.text)
        except ValueError:
            raise RuntimeError("Composio's answer was not what was expected") from None
        if result.is_error or not isinstance(data, dict):
            raise RuntimeError("Composio reported an error")
        return data


class ComposioGmail:
    def __init__(self, runner, clock: Callable[[], float] = time.time):
        self._runner, self._clock = runner, clock
        self.emails: dict[str, str] = {}  # account -> its own address, from Composio's list
        self._accounts: tuple[float, list[str]] = (0.0, [])

    def round(self):
        return self._runner.round()

    async def _execute(self, account: str, slug: str, arguments: dict) -> dict:
        body = {"tools": [{"tool_slug": slug, "arguments": arguments, "account": account}], "sync_response_to_workbench": False,
                "thought": "Minty's mail watcher (read only)"}
        if call_class(body, lambda ext_id: False) != 1:
            raise PermissionError(f"{slug} is not a read, so the mail watcher will not run it")
        answer = await self._runner.call(EXECUTE, body, max_chars=500_000)
        try:
            step = answer["data"]["results"][0]["response"]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError("Composio's answer had no result in it") from None
        if not step.get("successful"):
            error = str(step.get("error") or "unknown error")
            if slug == "GMAIL_LIST_HISTORY" and re.search(r"\b404\b|not found|too old|expired", error, re.IGNORECASE):
                raise HistoryExpired(error)
            raise RuntimeError(f"{slug}: {error[:120]}")
        return step.get("data") or {}

    async def accounts(self) -> list[str]:
        when, cached = self._accounts
        if cached and self._clock() - when < ACCOUNTS_FOR_S:
            return list(cached)
        try:
            answer = await self._runner.call(SEARCH, {"queries": [{"use_case": "read the gmail inbox"}]})
        except PermissionError:
            raise PermissionError("To find your Gmail accounts I need COMPOSIO_SEARCH_TOOLS allowed (minty mcp allow composio COMPOSIO_SEARCH_TOOLS "
                                  "--readonly), or name the accounts in gmail_watch.accounts") from None
        found, emails = [], {}
        for status in (answer.get("data") or {}).get("toolkit_connection_statuses") or []:
            if str(status.get("toolkit")).lower() != "gmail":
                continue
            for acct in status.get("accounts") or []:
                if str(acct.get("status")).upper() != "ACTIVE":
                    continue
                alias = str(acct.get("alias") or acct.get("id") or "")
                if alias:
                    found.append(alias)
                    emails[alias] = str((acct.get("user_info") or {}).get("email") or "").lower()
        self.emails = {a: e for a, e in emails.items() if e}
        self._accounts = (self._clock(), found)
        return list(found)

    async def baseline(self, account: str) -> str:
        data = await self._execute(account, "GMAIL_GET_PROFILE", {})
        if not data.get("historyId"):
            raise RuntimeError("Gmail gave no history position")
        return str(data["historyId"])

    async def new_messages(self, account: str, history_id: str) -> tuple[list[dict], str]:
        items, new_history, page = [], str(history_id), None
        for _ in range(5):
            args = {"start_history_id": str(history_id), "history_types": ["messageAdded"], "max_results": 100}
            if page:
                args["page_token"] = page
            data = await self._execute(account, "GMAIL_LIST_HISTORY", args)
            for entry in data.get("history") or []:
                for added in entry.get("messagesAdded") or []:
                    message = added.get("message") or {}
                    if message.get("id"):
                        items.append({"id": str(message["id"]), "thread": str(message.get("threadId") or message["id"]),
                                      "labels": [str(x) for x in message.get("labelIds") or []]})
            new_history = str(data.get("historyId") or new_history)
            page = data.get("nextPageToken")
            if not page:
                break
        return items, new_history

    async def recent_messages(self, account: str, minutes: int) -> list[dict]:
        since = int(self._clock() - 60 * int(minutes))
        data = await self._execute(account, "GMAIL_FETCH_EMAILS", {
            "query": f"in:inbox -category:promotions -category:social -category:forums after:{since}", "max_results": 50, "ids_only": True})
        return [{"id": str(m["messageId"]), "thread": str(m.get("threadId") or m["messageId"]), "labels": ["INBOX"]}
                for m in data.get("messages") or [] if m.get("messageId")]

    async def sender_known(self, account: str, address: str) -> bool:
        if not STRICT_ADDRESS.fullmatch(address or ""):
            raise ValueError("that is not an address I will put in a search")
        data = await self._execute(account, "GMAIL_FETCH_EMAILS", {"query": f"in:sent to:{address}", "max_results": 1, "ids_only": True})
        return bool(data.get("messages"))

    async def metadata(self, account: str, message_id: str) -> Mail:
        data = await self._execute(account, "GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID", {"message_id": message_id, "format": "metadata"})
        headers = {str(h.get("name", "")).lower(): str(h.get("value", ""))[:400] for h in (data.get("payload") or {}).get("headers") or []
                   if isinstance(h, dict) and str(h.get("name", "")).lower() in KEEP_HEADERS}
        preview = data.get("preview") or {}
        try:
            stamp = datetime.fromisoformat(str(data.get("messageTimestamp") or "").replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
        except ValueError:
            stamp = 0.0
        return Mail(id=str(data.get("messageId") or message_id), thread_id=str(data.get("threadId") or message_id),
                    sender=str(data.get("sender") or "")[:200], subject=str(data.get("subject") or preview.get("subject") or "")[:200],
                    snippet=str(preview.get("body") or data.get("messageText") or "")[:300],
                    to=tuple(a.lower() for a in ADDRESS.findall(str(data.get("to") or ""))), labels=tuple(str(x) for x in data.get("labelIds") or []),
                    headers=headers, date_ts=stamp)


def build_gmail_watcher(store, registry: McpRegistry, secret: Callable[[str], str | None], notifier, watch_store, halted: Callable[[], bool] = lambda: False,
                        prefs: Callable[[str], str | None] = lambda key: None):
    """The watcher as the app uses it: your settings, the Composio server you named, and what you have allowed."""
    from minty_core.gmailwatch import GmailWatcher, WatchSettings

    runner = ComposioRunner(registry, secret, server=store.config.gmail_watch.server, needs=(EXECUTE,), optional=(SEARCH,))
    source = ComposioGmail(runner)

    def settings() -> WatchSettings:
        c = store.config.gmail_watch
        return WatchSettings(enabled=c.enabled, accounts=tuple(c.accounts), me=dict(source.emails), interval_s=c.interval_s, vips=tuple(c.vips),
                             topics=tuple(c.topics), max_per_poll=c.max_per_poll, min_confidence=c.min_confidence, lookup_senders=c.lookup_senders)

    return GmailWatcher(source, watch_store, notifier, settings, halted=halted, prefs=prefs)
