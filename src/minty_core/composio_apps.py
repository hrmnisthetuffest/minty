"""Which apps are connected through Composio, remembered by itself.

Composio answers every COMPOSIO_SEARCH_TOOLS call with the connection state of the apps that call was about (`toolkit_connection_statuses`),
and has no "list everything" call. So the inventory is learned from every such answer (what Minty reads while it works) and, once in a while, by
asking about the common apps in a few read-only batches. It is only names and how many accounts, kept in a small file: it goes into the model's
hint and into the choice of tools, so that "upload this to YouTube" is understood without the user explaining that Composio can do it.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable

SEARCH = "COMPOSIO_SEARCH_TOOLS"
NAME = re.compile(r"[a-z][a-z0-9_]{1,39}")  # an app's name ends up in the model's hint, so it has to look like one
CHUNK = 6  # Composio takes at most 7 queries in one call
SEED = ("gmail", "googlecalendar", "googledrive", "googledocs", "googlesheets", "googleslides", "youtube", "slack", "github", "gitlab", "notion",
        "linear", "jira", "trello", "asana", "clickup", "todoist", "discord", "telegram", "whatsapp", "twitter", "instagram", "facebook", "linkedin",
        "tiktok", "reddit", "spotify", "dropbox", "onedrive", "outlook", "microsoft_teams", "zoom", "airtable", "hubspot", "salesforce", "stripe",
        "shopify", "figma", "canva", "calendly", "mailchimp", "sendgrid", "twilio", "supabase", "vercel", "wordpress", "medium", "pinterest", "twitch")


class AppsStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path, self._clock, self._lock = Path(path), clock, threading.Lock()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._apps = {k: v for k, v in data.items() if NAME.fullmatch(k) and isinstance(v, dict)}
        except (OSError, ValueError, AttributeError):
            self._apps = {}

    def learn(self, answer) -> int:
        """Take what an answer says about connections; returns how many apps it spoke of."""
        data = answer.get("data") if isinstance(answer, dict) else None
        statuses = data.get("toolkit_connection_statuses") if isinstance(data, dict) else None
        if not isinstance(statuses, list):
            return 0
        seen = 0
        with self._lock:
            for status in statuses:
                if not isinstance(status, dict):
                    continue
                name = str(status.get("toolkit") or "").strip().lower()
                if not NAME.fullmatch(name):
                    continue
                accounts = status.get("accounts")
                count = len([a for a in accounts if isinstance(a, dict) and str(a.get("status", "ACTIVE")).upper() == "ACTIVE"]) if isinstance(accounts, list) else 0
                active = bool(status.get("has_active_connection")) and (count > 0 or not isinstance(accounts, list))
                self._apps[name] = {"active": active, "accounts": max(count, 1) if active else 0, "ts": self._clock()}
                seen += 1
            if seen:
                self._save()
        return seen

    def connected(self) -> dict[str, int]:
        with self._lock:
            return {name: int(v.get("accounts") or 1) for name, v in sorted(self._apps.items()) if v.get("active")}

    def names(self) -> set[str]:
        return set(self.connected())

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._apps, f)
        except OSError:
            pass  # remembered for this run at least


async def refresh(runner, store: AppsStore, names: tuple[str, ...] = SEED) -> int:
    """Ask about the common apps (read-only, a few at a time) and remember which are connected. Returns how many apps it heard about; 0 when
    Composio cannot be reached or COMPOSIO_SEARCH_TOOLS is not allowed (this is never an error for the user)."""
    heard = 0
    try:
        async with runner.round():
            for i in range(0, len(names), CHUNK):
                batch = names[i:i + CHUNK]
                data = await runner.call(SEARCH, {"queries": [{"use_case": f"use {n}"} for n in batch]})
                heard += store.learn(data)
    except Exception:  # noqa: BLE001 — offline, not allowed, or an odd answer: what was learned so far stays
        return heard
    return heard
