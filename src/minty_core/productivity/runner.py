"""The once-a-minute pass: ring due alarms, then push the day's plan, the wind-down and the end-of-day summary at
their own times. Each push is its own switch, goes out once a day, and is dropped if its time was slept through."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Awaitable, Callable

import numpy as np

from minty_core.audio.types import Audio
from minty_core.productivity.alarm import ramp
from minty_core.productivity.kit import ProductivityProvider
from minty_core.voice.earcons import tone

log = logging.getLogger(__name__)
LATE = timedelta(minutes=30)  # a push whose time passed longer ago than this is not sent
ROUNDS = 4  # the alarm chimes up to this many times (the first one rising), then gives up
GAP_S = 15


def alarm_chime(volume: float) -> Audio:
    """The "wake" earcon scaled to a volume (0.25 to 1.0 of its own, already modest, level)."""
    base = tone("wake")
    pcm = (np.frombuffer(base.pcm, dtype=np.int16).astype(np.float32) * max(0.0, min(1.0, volume))).astype(np.int16)
    return Audio(pcm.tobytes(), base.rate)


class DayRunner:
    def __init__(self, provider: ProductivityProvider, say: Callable[[str], object], urgent: Callable[[str], object],
                 chime: Callable[[float], Awaitable[None]], now: Callable[[], datetime] = datetime.now,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._p, self._say, self._urgent, self._chime = provider, say, urgent, chime
        self._now, self._sleep = now, sleep

    async def tick(self) -> None:
        if not self._p.active():
            return
        now = self._now()
        life = self._p.life()
        for alarm in await asyncio.to_thread(life.alarms.due, now):
            await self._ring(alarm, now)
        cfg = self._p._config().productivity
        for kind, on, at, make in (("plan", cfg.plan_push, cfg.plan_time, self._p.plan_text),
                                   ("wind_down", cfg.wind_down, cfg.wind_down_time, self._p.wind_down_text),
                                   ("summary", cfg.summary_push, cfg.summary_time, self._p.summary_text)):
            if on and self._due(kind, at, now):
                life.db.set_meta(f"sent_{kind}", now.date().isoformat())  # first: a failure must not repeat it all day
                self._say(await asyncio.to_thread(make, now.date()))

    def _due(self, kind: str, at: str, now: datetime) -> bool:
        if self._p.life().db.meta(f"sent_{kind}") == now.date().isoformat():
            return False
        h, m = (int(x) for x in at.split(":"))
        start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        return start <= now <= start + LATE

    async def _ring(self, alarm: dict, now: datetime) -> None:
        alarms = self._p.life().alarms
        alarms.start_ring(alarm["id"])
        briefed = False
        for rnd in range(ROUNDS):
            for volume in (ramp(5) if rnd == 0 else [1.0]):
                if alarm["id"] not in alarms.ringing:
                    return  # stopped or snoozed
                await self._chime(volume)
                await self._sleep(1.5)
            if alarm["id"] not in alarms.ringing:
                return
            if alarm["brief"] and not briefed:
                briefed = True
                self._urgent(await asyncio.to_thread(self._p.brief_for, now))
            await self._sleep(GAP_S)
        if alarm["id"] in alarms.ringing:
            alarms.stop()  # never rings for ever
