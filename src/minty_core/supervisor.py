"""Supervision for the voice loop: if it dies, it comes back on its own, a bounded number of times.

The microphone and speaker side is the part most likely to fail (a device unplugged, PortAudio erroring). It runs
under this watchdog so that failure restarts the voice alone, in about a second, without touching the core, the
panel or the jobs. Repeated failures stop the restarts and say so instead of looping forever.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Awaitable, Callable

log = logging.getLogger(__name__)


class VoiceSupervisor:
    def __init__(self, start: Callable[[], Awaitable[None]], *, notify: Callable[[str], None] | None = None,
                 delay_s: float = 1.0, max_restarts: int = 5, window_s: float = 300.0,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self._start, self._notify = start, notify
        self._delay, self._max, self._window = delay_s, max_restarts, window_s
        self._sleep, self._clock = sleep, clock
        self._times: deque[float] = deque()
        self.restarts = 0
        self.gave_up = False
        self._task: asyncio.Future | None = None
        self._requested = False

    def restart(self) -> bool:
        """Ask for a fresh start of the loop now (after a suspend/resume, say). False if nothing is running."""
        if self._task is None or self._task.done():
            return False
        self._requested = True
        self._task.cancel()
        return True

    async def run(self) -> None:
        while True:
            try:
                self._requested = False
                self._task = asyncio.ensure_future(self._start())
                await self._task
                reason = "the voice loop ended"
            except asyncio.CancelledError:
                if self._requested:  # restart() cancelled the loop, not the supervisor's own task
                    self._requested = False
                    await self._sleep(0)
                    continue
                if self._task is not None and not self._task.done():
                    self._task.cancel()
                raise
            except Exception as e:  # noqa: BLE001 — anything: a supervisor's whole job
                log.exception("the voice loop failed")
                reason = f"{type(e).__name__}: {e}"
            now = self._clock()
            self._times.append(now)
            while self._times and now - self._times[0] > self._window:
                self._times.popleft()
            if len(self._times) > self._max:
                self.gave_up = True
                self._say("My voice keeps failing, so I've stopped restarting it. "
                          "Check the microphone and speaker, then say restart or restart me.")
                log.error("voice loop gave up after %d failures in %.0f s (%s)", len(self._times), self._window, reason)
                return
            self.restarts += 1
            log.warning("restarting the voice loop (%s), attempt %d", reason, self.restarts)
            if len(self._times) == 1:
                self._say("My voice stopped for a moment; I'm bringing it back.")
            await self._sleep(self._delay)

    def _say(self, text: str) -> None:
        if self._notify is not None:
            try:
                self._notify(text)
            except Exception:  # noqa: BLE001
                log.warning("supervisor notice failed", exc_info=True)
