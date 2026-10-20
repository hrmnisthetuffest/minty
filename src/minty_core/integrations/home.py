"""Home Assistant scenes by spoken name (668) and an energy summary from your meters (671). The only thing this module ever
changes in the house is `scene.turn_on` on a scene entity; everything else is reading."""
from __future__ import annotations

import difflib
import re
from datetime import datetime, timedelta
from typing import Awaitable, Callable
from urllib.parse import quote

Request = Callable[[str, str, dict | None], Awaitable[object]]
_FILLER = {"turn", "on", "the", "scene", "mode", "please", "activate", "start", "set", "to", "run", "a", "my"}
_ENTITY = re.compile(r"scene\.[a-z0-9_]+")
UNITS = {"kwh": 1.0, "wh": 0.001, "mwh": 1000.0}


def _tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _FILLER]


def resolve_scene(spoken: str, scenes: list[tuple[str, str]]) -> tuple[str | None, list[str]]:
    """(entity_id, []) when one scene clearly matches; (None, [names]) when several do; (None, []) when none."""
    want = _tokens(spoken)
    if not want:
        return None, []
    scored = []
    for entity, name in scenes:
        have = _tokens(name)
        if want == have:
            score = 1.0
        elif set(want) <= set(have) or set(have) <= set(want):
            score = 0.9
        else:
            score = difflib.SequenceMatcher(None, " ".join(want), " ".join(have)).ratio()
        if score >= 0.6:
            scored.append((score, entity, name))
    if not scored:
        return None, []
    scored.sort(key=lambda t: -t[0])
    top = [t for t in scored if t[0] >= scored[0][0] - 0.1]
    if len(top) == 1:
        return top[0][1], []
    return None, sorted(t[2] for t in top)


def _usage(values: list[str], factor: float) -> float:
    """kWh used over a series of meter readings; a reset (a reading lower than the one before) starts a new baseline."""
    nums = []
    for v in values:
        try:
            nums.append(float(v))
        except ValueError:
            continue
    total = 0.0
    for a, b in zip(nums, nums[1:]):
        if b >= a:
            total += b - a
    return total * factor


class HomeAssistant:
    def __init__(self, request: Request):
        self._req = request

    async def scenes(self) -> list[tuple[str, str]]:
        out = []
        for s in await self._req("GET", "/api/states", None) or []:
            eid = str(s.get("entity_id", ""))
            if _ENTITY.fullmatch(eid):
                out.append((eid, str((s.get("attributes") or {}).get("friendly_name") or eid)))
        return out

    async def run_scene(self, spoken: str) -> str:
        scenes = await self.scenes()
        if not scenes:
            return "Home Assistant has no scenes."
        entity, options = resolve_scene(spoken, scenes)
        if entity is None and options:
            return "Which one: " + " or ".join(options) + "?"
        if entity is None:
            return f"I do not see a scene called {spoken!r}. Scenes: " + ", ".join(n for _, n in scenes[:12]) + "."
        await self._req("POST", "/api/services/scene/turn_on", {"entity_id": entity})
        return f"{dict(scenes)[entity]} is on."

    async def _day(self, meters: dict[str, tuple[str, float]], start: datetime, end: datetime) -> dict[str, float]:
        ids = ",".join(meters)
        path = (f"/api/history/period/{quote(start.isoformat())}?filter_entity_id={ids}&end_time={quote(end.isoformat())}"
                "&minimal_response&no_attributes")
        out = {eid: 0.0 for eid in meters}
        for series in await self._req("GET", path, None) or []:
            if not series:
                continue
            eid = series[0].get("entity_id")
            if eid in meters:
                out[eid] = _usage([str(p.get("state")) for p in series], meters[eid][1])
        return out

    async def energy(self, now: datetime, tariff: float | None = None) -> str:
        meters = {}
        for s in await self._req("GET", "/api/states", None) or []:
            a = s.get("attributes") or {}
            unit = str(a.get("unit_of_measurement", "")).lower()
            if a.get("device_class") == "energy" and unit in UNITS and str(s.get("entity_id", "")).startswith("sensor."):
                meters[s["entity_id"]] = (str(a.get("friendly_name") or s["entity_id"]), UNITS[unit])
        if not meters:
            return "Home Assistant has no energy meters (sensors with device class energy), so there is nothing to add up."
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today = await self._day(meters, midnight, now)
        yesterday = await self._day(meters, midnight - timedelta(days=1), midnight)
        t, y = sum(today.values()), sum(yesterday.values())
        cost = f" (₹{t * tariff:.0f} at ₹{tariff:g} per kWh)" if tariff else ""
        top = [(n, v) for n, v in sorted(((meters[e][0], v) for e, v in today.items()), key=lambda x: -x[1])[:3] if v > 0]
        tail = ("Biggest today: " + ", ".join(f"{n} {v:.1f} kWh" for n, v in top) + ".") if top else "Nothing used yet today."
        return f"Energy: {t:.1f} kWh today{cost}, {y:.1f} kWh yesterday. {tail}"
