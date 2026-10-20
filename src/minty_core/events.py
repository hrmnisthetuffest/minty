from __future__ import annotations

import asyncio


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[asyncio.Queue, frozenset | None] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, types=None) -> asyncio.Queue:
        """types: only deliver these event types (None = everything)."""
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subs[q] = frozenset(types) if types else None
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.pop(q, None)

    def publish(self, type_: str, **data) -> None:
        event = {"type": type_, **data}
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            if self._loop is not None and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._deliver, event)
                return
        self._deliver(event)

    def _deliver(self, event: dict) -> None:
        for q, types in list(self._subs.items()):
            if types is not None and event["type"] not in types:
                continue
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass
