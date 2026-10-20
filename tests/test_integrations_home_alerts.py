"""668 scenes by name, 671 energy summary, 670 sleep timer, 687 severe-weather alerts."""
import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from minty_core.integrations.alerts import WeatherWatch, derive
from minty_core.integrations.db import IntegrationsDB
from minty_core.integrations.home import HomeAssistant, resolve_scene
from minty_core.integrations.sleeptimer import SleepTimer
from minty_core.system.runner import Result

IST = ZoneInfo("Asia/Kolkata")

STATES = [
    {"entity_id": "scene.movie_night", "state": "scening", "attributes": {"friendly_name": "Movie Night"}},
    {"entity_id": "scene.study_mode", "state": "scening", "attributes": {"friendly_name": "Study Mode"}},
    {"entity_id": "scene.morning_lights", "state": "scening", "attributes": {"friendly_name": "Morning Lights"}},
    {"entity_id": "light.desk", "state": "on", "attributes": {"friendly_name": "Desk lamp"}},
    {"entity_id": "sensor.geyser_energy", "state": "1210.5", "attributes": {"friendly_name": "Geyser", "device_class": "energy",
                                                                           "unit_of_measurement": "kWh"}},
    {"entity_id": "sensor.ac_energy", "state": "3300000", "attributes": {"friendly_name": "AC", "device_class": "energy",
                                                                       "unit_of_measurement": "Wh"}},
    {"entity_id": "sensor.temperature", "state": "27", "attributes": {"device_class": "temperature"}},
]


class FakeHA:
    def __init__(self, history=None):
        self.calls, self.history = [], history or []

    async def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/api/states":
            return STATES
        if path.startswith("/api/history/period/"):
            return self.history
        return {}


def test_a_spoken_scene_name_finds_the_right_scene_and_asks_when_unsure():
    scenes = [(s["entity_id"], s["attributes"]["friendly_name"]) for s in STATES if s["entity_id"].startswith("scene.")]
    assert resolve_scene("movie night", scenes)[0] == "scene.movie_night"
    assert resolve_scene("turn on the Movie Night scene", scenes)[0] == "scene.movie_night"
    assert resolve_scene("study", scenes)[0] == "scene.study_mode"
    assert resolve_scene("morning", scenes)[0] == "scene.morning_lights"
    entity, options = resolve_scene("night", scenes + [("scene.night_light", "Night Light")])
    assert entity is None and sorted(options) == ["Movie Night", "Night Light"]
    assert resolve_scene("disco", scenes) == (None, [])


async def test_running_a_scene_sends_one_scene_service_call_and_nothing_else():
    ha = FakeHA()
    out = await HomeAssistant(ha.request).run_scene("movie night")
    assert out == "Movie Night is on."
    posts = [c for c in ha.calls if c[0] == "POST"]
    assert posts == [("POST", "/api/services/scene/turn_on", {"entity_id": "scene.movie_night"})]
    none = await HomeAssistant(FakeHA().request).run_scene("disco")
    assert "I do not see a scene called" in none and "Movie Night" in none


async def test_an_unclear_scene_name_asks_and_changes_nothing():
    ha = FakeHA()
    ha_states = STATES + [{"entity_id": "scene.night_light", "state": "x", "attributes": {"friendly_name": "Night Light"}}]

    async def request(method, path, body=None):
        ha.calls.append((method, path, body))
        return ha_states if path == "/api/states" else {}

    out = await HomeAssistant(request).run_scene("night")
    assert out.startswith("Which one") and "Movie Night" in out and not [c for c in ha.calls if c[0] == "POST"]


def history(*series):
    return [[{"entity_id": eid, "state": str(v), "last_changed": "2026-10-18T00:00:00+00:00"} for v in values]
            for eid, values in series]


async def test_the_energy_summary_adds_up_today_and_yesterday_per_meter_and_converts_units():
    now = datetime(2026, 10, 18, 21, 0, tzinfo=IST)
    # today: geyser 1210.5 -> 1213.7 (3.2 kWh); AC counter in Wh 3300000 -> 3302900 (2.9 kWh)
    ha = FakeHA(history(("sensor.geyser_energy", [1210.5, 1212.0, 1213.7]), ("sensor.ac_energy", [3300000, 3301000, 3302900])))
    out = await HomeAssistant(ha.request).energy(now, tariff=8.0)
    assert "6.1 kWh today" in out and "Geyser 3.2 kWh" in out and "AC 2.9 kWh" in out
    assert "₹49" in out  # 6.1 kWh at 8 per kWh
    paths = [c[1] for c in ha.calls if "history" in c[1]]
    assert paths and "filter_entity_id=sensor.geyser_energy,sensor.ac_energy" in paths[0] and "minimal_response" in paths[0]


async def test_a_meter_that_reset_does_not_give_a_negative_day_and_no_meters_is_said():
    now = datetime(2026, 10, 18, 21, 0, tzinfo=IST)
    ha = FakeHA(history(("sensor.geyser_energy", [10.0, 12.0, 0.5, 1.5]), ("sensor.ac_energy", [1000000, 1000000])))
    out = await HomeAssistant(ha.request).energy(now)
    assert "Geyser 3.0 kWh" in out and "-" not in out.split("today")[0]

    async def empty(method, path, body=None):
        return [] if path == "/api/states" else []

    assert "no energy meters" in (await HomeAssistant(empty).energy(now)).lower()


# ---- sleep timer ---------------------------------------------------------------------------------------------------
class Player:
    def __init__(self):
        self.calls, self.volume = [], 0.6

    def have(self, name):
        return True

    async def run(self, argv, timeout=None):
        self.calls.append(argv)
        if argv[:2] == ["wpctl", "get-volume"]:
            return Result(0, f"Volume: {self.volume:.2f}\n")
        if argv[:2] == ["wpctl", "set-volume"]:
            self.volume = float(argv[3]) if not argv[3].endswith("%") else self.volume
        return Result(0, "")


async def test_the_timer_waits_fades_pauses_and_puts_the_volume_back():
    clock = [0.0]
    p = Player()

    async def sleep(s):
        clock[0] += s

    t = SleepTimer(p, clock=lambda: clock[0], sleep=sleep, step_s=15)
    assert "30 minutes" in await t.start(30, fade_minutes=2)
    assert "left" in t.status() or "running" in t.status()
    await t._task
    sets = [float(c[3]) for c in p.calls if c[:2] == ["wpctl", "set-volume"]]
    fade = sets[:-1]
    assert fade == sorted(fade, reverse=True) and fade[0] < 0.6 and fade[-1] <= 0.05
    assert sets[-1] == 0.6 and ["playerctl", "-a", "pause"] in p.calls
    assert p.calls.index(["playerctl", "-a", "pause"]) < len(p.calls) - 1  # paused before the volume came back
    assert clock[0] >= 30 * 60 - 1 and t.status() == "No sleep timer is running."


async def test_cancelling_during_the_fade_restores_the_volume_and_does_not_pause():
    p = Player()
    ticks = {"n": 0}

    async def sleep(s):
        ticks["n"] += 1
        await asyncio.sleep(0)

    t = SleepTimer(p, clock=lambda: float(ticks["n"]) * 15, sleep=sleep, step_s=15)
    await t.start(5, fade_minutes=2)
    for _ in range(15):  # let it run into the fade (it begins after 12 slices of 15 seconds)
        await asyncio.sleep(0)
    assert await t.cancel()
    assert p.volume == 0.6 and ["playerctl", "-a", "pause"] not in p.calls
    assert not await t.cancel()


async def test_timer_limits_and_one_at_a_time():
    p = Player()

    async def sleep(s):
        await asyncio.sleep(0)

    t = SleepTimer(p, sleep=sleep)
    for bad in (0, -5, 9999):
        with pytest.raises(ValueError):
            await t.start(bad)
    await t.start(10)
    with pytest.raises(ValueError, match="already running"):
        await t.start(20)
    await t.cancel()


# ---- severe-weather alerts -----------------------------------------------------------------------------------------
def hourly(codes=None, rain=None, gust=None, temp=None, vis=None):
    n = 48
    times = [f"2026-10-{18 + h // 24:02d}T{h % 24:02d}:00" for h in range(n)]
    base = lambda v, default: [(v or {}).get(i, default) for i in range(n)]  # noqa: E731
    return {"hourly": {"time": times, "weather_code": base(codes, 1), "precipitation": base(rain, 0.0),
                       "wind_gusts_10m": base(gust, 10.0), "temperature_2m": base(temp, 30.0),
                       "visibility": base(vis, 20000.0)}}


def test_alerts_are_derived_from_forecast_numbers_with_times_and_plain_words():
    f = hourly(codes={15: 95, 16: 96, 17: 95}, rain={15: 9.0, 16: 12.0}, gust={16: 72.0}, temp={h: 43.0 for h in range(12, 17)})
    got = {a.kind: a for a in derive(f)}
    assert set(got) == {"thunderstorm", "heavy rain", "strong wind", "heat"}
    assert "15:00" in got["thunderstorm"].text and "hail" in got["thunderstorm"].text
    assert "21.0 mm" in got["heavy rain"].text and "72" in got["strong wind"].text and "43" in got["heat"].text
    assert all(a.key.startswith(a.kind) for a in got.values())


def test_calm_weather_gives_no_alerts_and_cold_and_fog_do():
    assert derive(hourly()) == []
    f = hourly(temp={h: 3.0 for h in (4, 5, 6)}, codes={6: 45, 7: 45, 8: 48}, vis={6: 150.0, 7: 150.0, 8: 300.0})
    assert {a.kind for a in derive(f)} == {"cold", "fog"}


class Net:
    def __init__(self, forecast):
        self.forecast, self.calls = forecast, []

    async def get(self, url, params=None):
        self.calls.append((url, params))
        if "geocoding" in url:
            return {"results": [{"name": "Paris", "latitude": 30.7, "longitude": 76.7}]}
        return self.forecast


async def test_each_alert_is_said_once_the_place_is_found_once_and_the_wording_is_honest(tmp_path):
    net = Net(hourly(codes={15: 95}))
    t = [1_760_000_000.0]
    w = WeatherWatch(net.get, IntegrationsDB(tmp_path / "i.db"), clock=lambda: t[0])
    first = await w.new_alerts("Paris")
    assert len(first) == 1 and "not an official warning" in first[0] and "Thunderstorm" in first[0]
    assert await w.new_alerts("Paris") == []
    geo = [c for c in net.calls if "geocoding" in c[0]]
    assert len(geo) == 1  # the place is looked up once and remembered
    forecast_call = next(c for c in net.calls if "forecast" in c[0])[1]
    assert forecast_call["latitude"] == 30.7 and forecast_call["timezone"] == "auto" and "wind_gusts_10m" in forecast_call["hourly"]
    t[0] += 4 * 86400
    assert len(await w.new_alerts("Paris")) == 1  # a new day's storm is news again


async def test_an_unknown_place_is_said_not_guessed(tmp_path):
    async def get(url, params=None):
        return {"results": []}

    w = WeatherWatch(get, IntegrationsDB(tmp_path / "i.db"))
    with pytest.raises(ValueError, match="could not find"):
        await w.alerts("Nowhereville")
