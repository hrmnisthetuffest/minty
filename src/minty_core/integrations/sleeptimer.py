"""A sleep timer for music and video (670): after N minutes the volume fades down over the last few and the player pauses, then
the volume is put back so tomorrow's first song is not silent. Cancelling restores the volume too. One timer at a time."""
from __future__ import annotations

import asyncio
import re
import time
from typing import Awaitable, Callable

from minty_core.system.runner import Runner, _wpctl_ok

SINK = "@DEFAULT_AUDIO_SINK@"


def make_runner() -> Runner:
    """Only the sound and player commands the timer uses."""
    return Runner(allowed={"wpctl", "playerctl"}, rules={
        "wpctl": _wpctl_ok, "playerctl": lambda a: a[1:] in (["-a", "pause"], ["pause"], ["status"])}, timeout=5.0)


class SleepTimer:
    def __init__(self, runner, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, step_s: float = 15.0):
        self.r, self._clock, self._sleep, self.step = runner, clock, sleep, max(1.0, step_s)
        self._task: asyncio.Task | None = None
        self._end = self._fade = 0.0

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _volume(self) -> float:
        res = await self.r.run(["wpctl", "get-volume", SINK])
        m = re.search(r"Volume:\s*([\d.]+)", res.out)
        return float(m.group(1)) if m else 0.5

    async def _set(self, v: float) -> None:
        await self.r.run(["wpctl", "set-volume", SINK, f"{max(0.0, min(1.0, v)):.2f}"])

    async def start(self, minutes: float, fade_minutes: float = 5.0) -> str:
        if self.running():
            raise ValueError("A sleep timer is already running. Cancel it first.")
        if not 1 <= float(minutes) <= 480:
            raise ValueError("A sleep timer is 1 to 480 minutes.")
        fade = max(0.5, min(float(fade_minutes), float(minutes)))
        now = self._clock()
        self._end, self._fade = now + minutes * 60, now + (minutes - fade) * 60
        self._task = asyncio.get_running_loop().create_task(self._run(await self._volume(), fade))
        return (f"Sleep timer set for {minutes:g} minutes. The sound fades over the last {fade:g} and then stops.")

    async def _run(self, original: float, fade_minutes: float) -> None:
        faded = False
        try:
            while self._clock() < self._fade:
                await self._sleep(min(self.step, max(0.1, self._fade - self._clock())))
            steps = max(1, int(fade_minutes * 60 / self.step))
            faded = True
            for i in range(1, steps + 1):
                await self._set(original * (1 - i / steps))
                await self._sleep(self.step)
            await self.r.run(["playerctl", "-a", "pause"])
            await self._set(original)
        except asyncio.CancelledError:
            if faded:
                await asyncio.shield(self._set(original))
            raise

    def status(self) -> str:
        if not self.running():
            return "No sleep timer is running."
        left = max(0.0, self._end - self._clock())
        return f"A sleep timer is running: {int(left // 60)} minutes {int(left % 60)} seconds left."

    async def cancel(self) -> bool:
        if not self.running():
            return False
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        return True
