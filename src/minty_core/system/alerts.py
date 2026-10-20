"""Battery, heat and CPU-hog alerts (635) that do not nag: each has a re-arm line (hysteresis) and a minimum gap, so a
battery hovering at 20 percent is mentioned once, not every minute."""
from __future__ import annotations

import time
from typing import Callable


class Alerts:
    def __init__(self, clock: Callable[[], float] = time.time, battery_low: int = 20, battery_critical: int = 10,
                 hot_c: float = 85.0, cool_c: float = 75.0, hog_percent: float = 85.0, hog_samples: int = 3,
                 min_gap_s: float = 1800.0, ignore: tuple[str, ...] = ()):
        self._clock, self.low, self.critical = clock, battery_low, battery_critical
        self.hot_c, self.cool_c, self.hog_percent, self.hog_samples = hot_c, cool_c, hog_percent, hog_samples
        self.min_gap, self.ignore = min_gap_s, tuple(n.lower() for n in ignore)
        self._armed = {"low": True, "critical": True, "hot": True}
        self._last: dict[str, float] = {}
        self._streak: dict[str, int] = {}

    def _may(self, key: str) -> bool:
        now = self._clock()
        if now - self._last.get(key, -1e12) < self.min_gap and key in self._last:
            return False
        self._last[key] = now
        return True

    def feed(self, *, battery: int | None, on_ac: bool | None, temp: float | None,
             hogs: list[tuple[str, float]]) -> list[str]:
        out: list[str] = []
        discharging = on_ac is False and battery is not None
        if discharging and battery <= self.critical:
            if self._armed["critical"]:
                self._armed["critical"] = False
                self._last["critical"] = self._clock()
                out.append(f"The battery is very low, {battery} percent. Plug in soon.")
        elif discharging and battery <= self.low:
            if self._armed["low"]:
                self._armed["low"] = False
                self._last["low"] = self._clock()
                out.append(f"The battery is at {battery} percent and not charging.")
        if on_ac or (battery is not None and battery >= self.low + 10):
            self._armed["low"] = self._armed["critical"] = True
        elif battery is not None and battery > self.critical + 5:
            self._armed["critical"] = True
        if temp is not None:
            if temp >= self.hot_c:
                if self._armed["hot"] and self._may("hot"):
                    out.append(f"The computer is running hot, {temp:.0f} degrees. Close heavy programs or give it air.")
                self._armed["hot"] = False
            elif temp <= self.cool_c:
                self._armed["hot"] = True
        busy = {n: pct for n, pct in hogs if pct >= self.hog_percent and n.lower() not in self.ignore}
        for name in list(self._streak):
            if name not in busy:
                del self._streak[name]
        for name, pct in busy.items():
            self._streak[name] = self._streak.get(name, 0) + 1
            if self._streak[name] >= self.hog_samples and self._may(f"hog:{name}"):
                out.append(f"{name} has been using most of a processor core ({pct:.0f} percent) for a while. Ask me what "
                           "it is, or end it yourself if you did not expect it.")
        return out
