"""Pushes to the phone: notices, briefs and finished jobs, held during quiet hours and delivered once afterwards.

The bus already carries everything Minty would say aloud ("notify") and every job change ("job"); this mirrors them
to the paired chats. Quiet hours (the night window) hold them; when the quiet ends they go out as
one digest, not a pile. Finished jobs are silent pings (no phone sound). A private job says only that it finished.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Callable

from minty_core.telegram.api import TelegramError

log = logging.getLogger(__name__)
MAX_HELD = 30


class PushRouter:
    def __init__(self, gateway, bus, *, quiet: Callable[[datetime], bool], state_path: Path,
                 clock: Callable[[], datetime] = datetime.now):
        self.gw, self.bus, self._quiet, self._clock = gateway, bus, quiet, clock
        self.state_path = Path(state_path)
        self._held: list[dict] = self._load()
        self._queue = bus.subscribe({"notify", "job", "phone"})
        self._seen_jobs: dict[int, str] = {}

    # ---- the held list survives a restart ----------------------------------------------------------
    def _load(self) -> list[dict]:
        try:
            return list(json.loads(self.state_path.read_text(encoding="utf-8")))[-MAX_HELD:]
        except (OSError, ValueError):
            return []

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.state_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._held[-MAX_HELD:], f)

    # ---- turning bus events into pushes -------------------------------------------------------------
    def from_event(self, event: dict) -> dict | None:
        if event.get("type") == "phone":  # the notifier decided this one goes to the phone (it weighed the quiet hours itself)
            return {"text": str(event.get("text") or ""), "silent": False, "force": True}
        if event.get("type") == "notify":
            if event.get("local"):  # said and shown here only: it may carry detail that should not leave the laptop
                return None
            if event.get("job_origin") == "laptop":  # a job asked for on the laptop reports on the laptop
                return None
            return {"text": str(event.get("text") or ""), "silent": False}
        if event.get("type") == "job":
            status, ident = event.get("status"), event.get("id")
            if event.get("origin") == "laptop":  # only a job asked for on Telegram pings the phone
                return None
            if status not in ("done", "failed", "timeout", "cancelled") or self._seen_jobs.get(ident) == status:
                return None
            self._seen_jobs[ident] = status
            if len(self._seen_jobs) > 200:
                self._seen_jobs.pop(next(iter(self._seen_jobs)))
            goal = str(event.get("goal") or "")[:60]
            verb = {"done": "finished", "failed": "failed", "timeout": "took too long", "cancelled": "was stopped"}[status]
            text = f"Job {ident} ({goal}) {verb}."
            if event.get("private"):
                text = f"Job {ident} {verb}. It was private, so the details are on the laptop."
            elif status == "done" and event.get("progress"):
                text += f" {str(event['progress'])[:200]}"
            return {"text": text, "silent": True}
        return None

    def is_quiet(self) -> bool:
        return bool(self._quiet(self._clock()))

    async def push(self, text: str, silent: bool = False, force: bool = False) -> bool:
        """Send now, or hold during quiet hours (unless forced by the notifier). True when it went out."""
        if not text.strip() or not self.gw.allowed or self.gw.suspended:
            return False
        if self._quiet(self._clock()) and not force:
            if not any(h["text"] == text for h in self._held):
                self._held.append({"text": text, "silent": silent})
                self._save()
            return False
        await self._send(text, silent)
        return True

    async def _send(self, text: str, silent: bool) -> None:
        for chat in sorted(self.gw.allowed):
            try:
                await self.gw.reply(chat, text, silent=silent)
            except TelegramError as e:
                log.warning("could not push to a chat: %s", e)

    async def release(self) -> int:
        """Quiet hours are over: deliver what was held, once, as one message."""
        if not self._held or self._quiet(self._clock()) or self.gw.suspended:
            return 0
        held, self._held = self._held, []
        self._save()
        if len(held) == 1:
            await self._send(held[0]["text"], held[0]["silent"])
        else:
            lines = "\n".join(f"{i}. {h['text']}" for i, h in enumerate(held, 1))
            await self._send(f"While you were away, {len(held)} things:\n{lines}", True)
        return len(held)

    # ---- the loop -------------------------------------------------------------------------------------
    async def run(self, poll_s: float = 30.0) -> None:
        while True:
            try:
                event = await asyncio.wait_for(self._queue.get(), poll_s)
            except asyncio.TimeoutError:
                await self.release()
                continue
            item = self.from_event(event)
            if item is not None:
                try:
                    await self.push(item["text"], item["silent"], force=bool(item.get("force")))
                except Exception:  # noqa: BLE001
                    log.warning("push failed", exc_info=True)
            await self.release()
