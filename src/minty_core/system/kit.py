"""One handle on the Linux-operations helpers for the plugin and the loops. Nothing runs until a `system.*` switch is on and
something asks. Every outside thing (commands, the news feed, /proc, the clock) can be replaced in tests."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from minty_core.system import osinfo
from minty_core.system.alerts import Alerts
from minty_core.system.controls import Controls
from minty_core.system.notify import NotificationReader
from minty_core.system.proc import CpuSampler, ResourceGate, read_procs
from minty_core.system.runner import Runner
from minty_core.system.usb import Usb

OFF = "The system helpers are switched off (system.enabled on the Switches page)."
OFF_CONTROLS = "Changing the computer is switched off (system.controls on the Switches page)."
OFF_NOTIFICATIONS = "The notification reader is switched off (system.notifications, and choose apps in system.notification_apps)."


class SystemOff(RuntimeError):
    pass


class SystemProvider:
    def __init__(self, config: Callable, directory: Path, services: dict, clock: Callable[[], float] = time.time,
                 runner=None, os_info: osinfo.OS | None = None, fetch_news=None, pacman_log: Path = Path("/var/log/pacman.log"),
                 proc_root: Path | None = None):
        self._config, self._dir, self._services, self._clock = config, Path(directory), services, clock
        self.runner = runner or Runner()
        self._os, self._fetch_news, self._pacman_log, self._proc_root = os_info, fetch_news, pacman_log, proc_root
        self._controls: Controls | None = None
        self._usb: Usb | None = None
        self._reader: NotificationReader | None = None
        self._sampler: CpuSampler | None = None
        self._alerts: Alerts | None = None
        self._gate: ResourceGate | None = None

    def need(self, part: str) -> None:
        cfg = self._config().system
        ok, message = {"read": (cfg.enabled, OFF), "controls": (cfg.enabled and cfg.controls, OFF_CONTROLS),
                       "notifications": (cfg.notifications and bool(cfg.notification_apps), OFF_NOTIFICATIONS)}[part]
        if not ok:
            raise SystemOff(message)

    @property
    def os(self) -> osinfo.OS:
        if self._os is None:
            self._os = osinfo.detect()
        return self._os

    def controls(self) -> Controls:
        if self._controls is None:
            self._controls = Controls(self.runner)
        return self._controls

    def usb(self) -> Usb:
        if self._usb is None:
            self._usb = Usb(self.runner)
        return self._usb

    def reader(self) -> NotificationReader:
        if self._reader is None:
            self._reader = NotificationReader(lambda: list(self._config().system.notification_apps), self._clock)
        return self._reader

    def sampler(self) -> CpuSampler:
        if self._sampler is None:
            kw = {"read": lambda: read_procs(self._proc_root)} if self._proc_root else {}
            self._sampler = CpuSampler(**kw)
        return self._sampler

    def alerts(self) -> Alerts:
        cfg = self._config().system
        if self._alerts is None:
            self._alerts = Alerts(self._clock, battery_low=cfg.low_battery, hog_percent=cfg.hog_percent,
                                  ignore=("minty_core", "python", "python3"))
        return self._alerts

    def gate(self) -> ResourceGate:
        if self._gate is None:
            from minty_core.power import read_battery, read_temperature

            def power():
                on_ac, pct = read_battery()
                temp = read_temperature()
                return on_ac, pct, bool(temp is not None and temp >= 85.0)

            self._gate = ResourceGate(power=power)
        return self._gate

    async def news(self) -> str:
        if self._fetch_news is not None:
            return await self._fetch_news()
        from minty_core.system.updates import NEWS_FEED
        from minty_core.web.safeopen import fetch_clean

        got = await fetch_clean(NEWS_FEED)
        if got.status >= 400:
            raise RuntimeError(f"HTTP {got.status}")
        return got.data.decode(got.charset or "utf-8", "replace")

    def pacman_log(self) -> str:
        try:
            return self._pacman_log.read_text(errors="replace")[-400_000:]
        except OSError:
            return ""
