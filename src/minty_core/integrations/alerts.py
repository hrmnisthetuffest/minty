"""Severe-weather alerts (687), derived from the Open-Meteo forecast for your place. These are forecast numbers turned into plain
words, **not official warnings** (there is no free official feed I can rely on), and every message says so. Each alert is said
once per kind per day."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from minty_core.integrations.db import IntegrationsDB

GEO = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
HOURLY = "weather_code,precipitation,wind_gusts_10m,temperature_2m,visibility"
RAIN_HOUR_MM, RAIN_SPELL_MM, GUST_KMH, HEAT_C, COLD_C, FOG_M = 7.5, 2.5, 60.0, 40.0, 4.0, 200.0


@dataclass
class WeatherAlert:
    kind: str
    key: str
    start: str
    text: str


def _spans(flags: list[bool]) -> list[tuple[int, int]]:
    out, i = [], 0
    while i < len(flags):
        if flags[i]:
            j = i
            while j + 1 < len(flags) and flags[j + 1]:
                j += 1
            out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def derive(forecast: dict) -> list[WeatherAlert]:
    h = forecast.get("hourly") or {}
    times = h.get("time") or []
    n = len(times)
    get = lambda name, default: (h.get(name) or [default] * n)  # noqa: E731
    code, rain, gust, temp, vis = (get("weather_code", 0), get("precipitation", 0.0), get("wind_gusts_10m", 0.0),
                                   get("temperature_2m", 20.0), get("visibility", 20000.0))
    hhmm = lambda i: times[i][11:16]  # noqa: E731
    day = lambda i: times[i][:10]  # noqa: E731
    found: list[WeatherAlert] = []

    def add(kind: str, i: int, text: str) -> None:
        key = f"{kind}:{day(i)}"
        if not any(a.key == key for a in found):
            found.append(WeatherAlert(kind, key, times[i], text))

    for a, b in _spans([c in (95, 96, 99) for c in code]):
        hail = any(c in (96, 99) for c in code[a:b + 1])
        add("thunderstorm", a, f"Thunderstorm expected from {hhmm(a)} to {hhmm(b)}" + (" (hail is possible)." if hail else "."))
    if any(r >= RAIN_HOUR_MM for r in rain):
        for a, b in _spans([r >= RAIN_SPELL_MM for r in rain]):
            if any(r >= RAIN_HOUR_MM for r in rain[a:b + 1]):
                add("heavy rain", a, f"Heavy rain: about {sum(rain[a:b + 1]):.1f} mm between {hhmm(a)} and {hhmm(b)}.")
    for a, b in _spans([g >= GUST_KMH for g in gust]):
        add("strong wind", a, f"Strong gusts up to {max(gust[a:b + 1]):.0f} km/h around {hhmm(a)}.")
    for a, b in _spans([t >= HEAT_C for t in temp]):
        add("heat", a, f"Very hot: up to {max(temp[a:b + 1]):.0f}°C between {hhmm(a)} and {hhmm(b)}. Stay inside and drink water.")
    for a, b in _spans([t <= COLD_C for t in temp]):
        add("cold", a, f"Cold: down to {min(temp[a:b + 1]):.0f}°C between {hhmm(a)} and {hhmm(b)}.")
    for a, b in _spans([c in (45, 48) or v < FOG_M for c, v in zip(code, vis)]):
        add("fog", a, f"Dense fog around {hhmm(a)} to {hhmm(b)}; take care on the road.")
    return sorted(found, key=lambda a: a.start)


class WeatherWatch:
    def __init__(self, get: Callable[[str, dict | None], Awaitable[dict]], db: IntegrationsDB,
                 clock: Callable[[], float] = time.time):
        self._get, self.db, self._clock = get, db, clock

    async def resolve(self, place: str) -> tuple[float, float, str]:
        key = "geo:" + " ".join(str(place).lower().split())
        cached = self.db.meta(key)
        if cached:
            d = json.loads(cached)
            return d["lat"], d["lon"], d["name"]
        data = await self._get(GEO, {"name": place, "count": 1})
        results = (data or {}).get("results") or []
        if not results:
            raise ValueError(f"I could not find a place called {place}.")
        r = results[0]
        self.db.set_meta(key, json.dumps({"lat": r["latitude"], "lon": r["longitude"], "name": r.get("name", place)}))
        return r["latitude"], r["longitude"], r.get("name", place)

    async def alerts(self, place: str) -> tuple[str, list[WeatherAlert]]:
        lat, lon, name = await self.resolve(place)
        data = await self._get(FORECAST, {"latitude": lat, "longitude": lon, "hourly": HOURLY, "forecast_days": 2,
                                          "timezone": "auto", "wind_speed_unit": "kmh"})
        return name, derive(data)

    async def new_alerts(self, place: str) -> list[str]:
        name, found = await self.alerts(place)
        now = self._clock()
        with self.db.lock:
            self.db.db.execute("DELETE FROM seen_alerts WHERE at < ?", (now - 3 * 86400,))
            seen = {r["key"] for r in self.db.db.execute("SELECT key FROM seen_alerts")}
            fresh = [a for a in found if a.key not in seen]
            self.db.db.executemany("INSERT OR IGNORE INTO seen_alerts(key, at) VALUES (?,?)", [(a.key, now) for a in fresh])
            self.db.db.commit()
        return [f"Weather watch for {name} (from the forecast, not an official warning): {a.text}" for a in fresh]
