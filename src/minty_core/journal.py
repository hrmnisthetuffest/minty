"""Crash-safe conversation journal: the live conversation is saved after every turn.

After an unclean exit Minty asks "Do you want to continue the previous conversation?" and restores it only on a
yes; anything else starts fresh and keeps the old one in `journal-archive/`. Nothing is ever restored silently.
Messages marked private are not written to this file at all.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

from minty_core.llm.types import Message, ToolCall

MAX_MESSAGES = 300  # a Telegram chat is kept long (turn.chat_keep_messages)
KEEP_ARCHIVED = 5


def _dump(m: Message) -> dict:
    return {"role": m.role, "content": m.content, "tool_call_id": m.tool_call_id, "name": m.name, "ts": m.ts, "chat": m.chat,
            "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments, "extra": c.extra}
                           for c in m.tool_calls]}


def _load(d: dict) -> Message:
    calls = [ToolCall(str(c["id"]), str(c["name"]), dict(c.get("arguments") or {}), dict(c.get("extra") or {}))
             for c in d.get("tool_calls") or []]
    return Message(d["role"], str(d.get("content") or ""), calls, d.get("tool_call_id"), d.get("name"), False,
                   float(d.get("ts") or 0.0), bool(d.get("chat")))


class ConversationJournal:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time, max_messages: int = MAX_MESSAGES):
        self.path = Path(path)
        self._clock, self._max = clock, int(max_messages)

    # ---- writing -------------------------------------------------------------------------------
    def save(self, history: list[Message]) -> None:
        kept = [m for m in history if not m.private and m.role != "system"]
        kept = kept[-self._max:]
        while kept and kept[0].role != "user":  # never start in the middle of a tool exchange
            kept.pop(0)
        self._write({"clean": False, "saved": self._clock(), "messages": [_dump(m) for m in kept]})

    def mark_clean(self) -> None:
        data = self._read()
        if data is not None:
            data["clean"] = True
            self._write(data)

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        tmp.replace(self.path)

    def _read(self) -> dict | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    # ---- after a restart ---------------------------------------------------------------------------
    def pending(self) -> list[Message] | None:
        """The conversation of an unclean exit, or None (clean exit, nothing said, or nothing saved)."""
        data = self._read()
        if not data or data.get("clean") or not data.get("messages"):
            return None
        try:
            return [_load(d) for d in data["messages"]]
        except (KeyError, TypeError, ValueError):
            return None

    def archive(self) -> Path | None:
        """Set the saved conversation aside (kept, newest five) and start empty."""
        if not self.path.exists():
            return None
        folder = self.path.parent / "journal-archive"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(self._clock()))}.json"
        self.path.replace(target)
        target.chmod(0o600)
        for old in sorted(folder.glob("*.json"))[:-KEEP_ARCHIVED]:
            old.unlink(missing_ok=True)
        return target


class ResumeQuestion:
    """Holds the one question asked after an unclean exit and applies the answer."""

    QUESTION = "I wasn't closed properly last time. Do you want to continue the previous conversation?"

    def __init__(self, agent, journal: ConversationJournal, messages: list[Message]):
        self._agent, self._journal, self._messages = agent, journal, messages
        self.pending = bool(messages)

    def accept(self) -> str:
        self.pending = False
        self._agent.history[:0] = self._messages
        return "Okay, continuing where we left off."

    def decline(self) -> str:
        self.pending = False
        self._journal.archive()
        return "Okay, starting fresh. The old conversation is kept."
