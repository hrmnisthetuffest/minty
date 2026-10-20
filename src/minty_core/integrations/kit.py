"""One handle on the integrations for the plugin and the loop. Nothing is created on disk until `integrations.enabled` is on and
something asks. Every outside thing (feeds, Home Assistant, the player, the forecast, ping) can be replaced in tests."""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from minty_core.integrations.alerts import WeatherWatch
from minty_core.integrations.db import IntegrationsDB
from minty_core.integrations.home import HomeAssistant
from minty_core.integrations.ics import CalendarStore
from minty_core.integrations.lan import Lan, make_runner as make_lan_runner
from minty_core.integrations.sleeptimer import SleepTimer, make_runner as make_player_runner

OFF = "The integrations are switched off (integrations.enabled on the Switches page)."
PART_OFF = {"calendar": "The calendar is switched off (integrations.calendar).",
            "home": "Home Assistant control is switched off (integrations.home).",
            "sleep_timer": "The sleep timer is switched off (integrations.sleep_timer).",
            "weather_alerts": "Weather alerts are switched off (integrations.weather_alerts).",
            "lan": "The network inventory is switched off (integrations.lan, and list integrations.lan_ranges)."}
NOT_HA = "Home Assistant isn't set up. Put its address and token in the smart_home plugin's settings first."
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class IntegrationsOff(RuntimeError):
    pass


def parse_day(text: str, today: date) -> date:
    t = " ".join(str(text or "today").lower().split())
    if t in ("", "today"):
        return today
    if t == "tomorrow":
        return today + timedelta(days=1)
    if t == "yesterday":
        return today - timedelta(days=1)
    if t in DAYS or t[:3] in {d[:3] for d in DAYS}:
        want = next(i for i, d in enumerate(DAYS) if d == t or d[:3] == t[:3])
        return today + timedelta(days=(want - today.weekday()) % 7 or 7)
    try:
        return date.fromisoformat(t)
    except ValueError:
        raise ValueError("Say today, tomorrow, a weekday or a date like 2026-10-20.") from None


async def _default_fetch(url: str) -> str:
    from minty_core.web.safeopen import fetch_clean

    got = await fetch_clean(url, max_bytes=2_000_000)
    if got.status >= 400:
        raise RuntimeError(f"HTTP {got.status}")
    return got.data.decode(got.charset or "utf-8", "replace")


async def _default_get_json(url: str, params: dict | None = None) -> dict:
    from minty_core.httpclient import get_client

    resp = await get_client().get(url, params=params, timeout=20.0)
    resp.raise_for_status()
    return resp.json()


class IntegrationsProvider:
    def __init__(self, config: Callable, directory: Path, services: dict, clock: Callable[[], float] = time.time,
                 secret: Callable[[str], str | None] = lambda name: None, fetch=None, ha_request=None, get_json=None,
                 player_runner=None, lan_runner=None, sleep=None):
        self._config, self._dir, self._services, self._clock, self._secret = config, Path(directory), services, clock, secret
        self._fetch, self._ha_request, self._get_json = fetch or _default_fetch, ha_request, get_json or _default_get_json
        self._player, self._lan_runner, self._sleep = player_runner, lan_runner, sleep
        self._db: IntegrationsDB | None = None
        self._timer: SleepTimer | None = None

    def need(self, part: str = "") -> None:
        cfg = self._config().integrations
        if not cfg.enabled:
            raise IntegrationsOff(OFF)
        if part and not getattr(cfg, part):
            raise IntegrationsOff(PART_OFF[part])

    def db(self) -> IntegrationsDB:
        if self._db is None:
            self._db = IntegrationsDB(self._dir / "integrations.db")
        return self._db

    def tz(self):
        try:
            return ZoneInfo(self._config().integrations.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return timezone.utc

    def now(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), self.tz())

    def calendar(self) -> CalendarStore:
        return CalendarStore(self.db(), self.tz(), self._clock)

    async def fetch(self, url: str) -> str:
        return await self._fetch(url)

    def home(self) -> HomeAssistant:
        if self._ha_request is not None:
            return HomeAssistant(self._ha_request)
        entry = self._config().plugins.get("smart_home", {})
        url, token = str(entry.get("url") or "").rstrip("/"), self._secret("plugin.smart_home.token")
        if not (url and token):
            raise IntegrationsOff(NOT_HA)

        async def request(method: str, path: str, body: dict | None = None):
            from minty_core.httpclient import get_client

            resp = await get_client().request(method, url + path, json=body, headers={"Authorization": f"Bearer {token}"}, timeout=15)
            if resp.status_code in (401, 403):
                raise RuntimeError("Home Assistant rejected the token")
            resp.raise_for_status()
            return resp.json() if resp.content else {}

        return HomeAssistant(request)

    def timer(self) -> SleepTimer:
        if self._timer is None:
            kw = {"sleep": self._sleep} if self._sleep else {}
            self._timer = SleepTimer(self._player or make_player_runner(), self._clock, **kw)
        return self._timer

    def weather(self) -> WeatherWatch:
        return WeatherWatch(self._get_json, self.db(), self._clock)

    def lan(self) -> Lan:
        return Lan(self._lan_runner or make_lan_runner(), self.db(), self._clock)

    def n8n_folder(self) -> Path:
        return self._dir / "n8n-templates"

    async def tick(self) -> list[str]:
        """Refresh calendars hourly and say new weather alerts (every three hours). Quiet when a switch is off."""
        cfg = self._config().integrations
        if not cfg.enabled:
            return []
        out: list[str] = []
        now = self._clock()
        if cfg.calendar and now - float(self.db().meta("cal_refresh", "0") or 0) >= 3600:
            self.db().set_meta("cal_refresh", str(now))
            store = self.calendar()
            for feed in store.feeds():
                try:
                    await store.refresh(feed["id"], self.fetch)
                except Exception:  # noqa: BLE001 — a feed that is down is retried next hour
                    continue
        if cfg.weather_alerts and cfg.location and now - float(self.db().meta("wx_check", "0") or 0) >= 3 * 3600:
            self.db().set_meta("wx_check", str(now))
            try:
                out += await self.weather().new_alerts(cfg.location)
            except Exception:  # noqa: BLE001
                pass
        return out
