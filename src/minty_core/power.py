"""Power, heat and the clock: notice a suspend/resume, battery state and a hot machine, from sysfs only.

On Linux the monotonic clock stops during suspend while the wall clock does not, so a gap between the two means the
laptop slept. After a resume the audio devices and the network are re-opened/re-probed by the caller. All readers are
injectable so the logic runs in tests without a laptop.
"""
from __future__ import annotations

import glob
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SUPERPOWER = "/sys/class/power_supply"
THERMAL = "/sys/class/thermal"


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def read_battery(root: str = SUPERPOWER) -> tuple[bool | None, int | None]:
    """(on AC power?, battery percent), None where there is no such supply (a desktop)."""
    on_ac, percent = None, None
    for supply in sorted(glob.glob(f"{root}/*")):
        kind = _read(f"{supply}/type")
        if kind == "Mains":
            on_ac = _read(f"{supply}/online") == "1"
        elif kind == "Battery" and _read(f"{supply}/capacity").isdigit():
            percent = int(_read(f"{supply}/capacity"))
            if on_ac is None:
                on_ac = _read(f"{supply}/status") in ("Charging", "Full")
    return on_ac, percent


def read_temperature(root: str = THERMAL) -> float | None:
    """The hottest thermal zone in degrees Celsius, or None."""
    hottest = None
    for zone in glob.glob(f"{root}/thermal_zone*/temp"):
        raw = _read(zone)
        if raw.lstrip("-").isdigit():
            celsius = int(raw) / 1000.0
            hottest = celsius if hottest is None else max(hottest, celsius)
    return hottest


@dataclass
class PowerState:
    on_ac: bool | None = None
    battery: int | None = None
    temperature: float | None = None
    hot: bool = False
    low_battery: bool = False

    def heavy_work_ok(self) -> bool:
        """Local models, compaction and backups wait while it is hot or the battery is nearly out."""
        return not (self.hot or self.low_battery)


class PowerMonitor:
    def __init__(self, *, battery: Callable[[], tuple[bool | None, int | None]] = read_battery,
                 temperature: Callable[[], float | None] = read_temperature,
                 wall: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic,
                 hot_c: float = 85.0, cool_c: float = 75.0, low_pct: int = 15, resume_gap_s: float = 10.0):
        self._battery, self._temperature, self._wall, self._mono = battery, temperature, wall, mono
        self.hot_c, self.cool_c, self.low_pct, self.resume_gap_s = hot_c, cool_c, low_pct, resume_gap_s
        self._last_wall, self._last_mono = wall(), mono()
        self.state = PowerState()

    def check_resume(self) -> float | None:
        """Seconds the machine slept (or the clock was set forward) since the last call, else None."""
        wall, mono = self._wall(), self._mono()
        slept = (wall - self._last_wall) - (mono - self._last_mono)
        self._last_wall, self._last_mono = wall, mono
        return slept if slept >= self.resume_gap_s else None

    def clock_went_back(self, previous_wall: float, wall: float, tolerance_s: float = 60.0) -> bool:
        return wall < previous_wall - tolerance_s

    def sample(self) -> PowerState:
        on_ac, percent = self._battery()
        temp = self._temperature()
        hot = self.state.hot  # hysteresis: hot above hot_c, cool again only below cool_c
        if temp is not None:
            hot = temp >= self.hot_c or (hot and temp > self.cool_c)
        self.state = PowerState(on_ac, percent, temp, hot, bool(percent is not None and percent <= self.low_pct
                                                                and on_ac is False))
        return self.state

    def words(self) -> str:
        s = self.state
        parts = []
        if s.battery is not None:
            parts.append(f"The battery is at {s.battery} percent" + (" and charging" if s.on_ac else
                         " and not charging" if s.on_ac is False else ""))
        else:
            parts.append("This computer has no battery")
        if s.temperature is not None:
            parts.append(f"it is running at {s.temperature:.0f} degrees" + (" and hot" if s.hot else ""))
        return ", ".join(parts) + "."
