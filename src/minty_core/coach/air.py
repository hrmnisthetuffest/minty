"""An air-quality note before outdoor training (856): Open-Meteo's free air-quality API, no key, off until you give a
latitude and longitude. It is the only network call in the coach; it sends your coordinates and nothing else."""
from __future__ import annotations

import httpx

URL = "https://air-quality-api.open-meteo.com/v1/air-quality"


def _words(aqi: float) -> str:
    n = round(aqi)
    if n <= 50:
        return f"Air quality is fine (US AQI {n})."
    if n <= 100:
        return f"Air quality is moderate (US AQI {n}): fine for most people; keep it easy if you're sensitive."
    if n <= 150:
        return f"Air quality is poor for sensitive people (US AQI {n}): shorten or ease any outdoor session."
    return f"Air quality is bad (US AQI {n}): train indoors today."


async def air_note(client: httpx.AsyncClient, latitude: float, longitude: float) -> str:
    try:
        resp = await client.get(URL, params={"latitude": f"{latitude:.2f}", "longitude": f"{longitude:.2f}",
                                              "current": "us_aqi,pm2_5"}, timeout=10.0)
        resp.raise_for_status()
        aqi = resp.json()["current"]["us_aqi"]
        return _words(float(aqi))
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        return "I couldn't check the air quality just now."
