"""Background tasks nobody awaits. The event loop keeps only a weak reference to a task, so one that nothing else holds can be
collected before it finishes; `spawn` holds it until it is done and logs a failure instead of losing it."""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)
_running: set[asyncio.Task] = set()


def spawn(coro, name: str | None = None) -> asyncio.Task:
    task = asyncio.ensure_future(coro)
    if name and hasattr(task, "set_name"):
        task.set_name(name)
    _running.add(task)

    def done(finished: asyncio.Task) -> None:
        _running.discard(finished)
        if finished.cancelled():
            return
        error = finished.exception()
        if error is not None:
            log.error("background task %s failed: %s", name or finished.get_name(), error, exc_info=error)

    task.add_done_callback(done)
    return task
