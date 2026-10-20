"""The integration tools as the model sees them, through the real plugin host (everything outside is a fake)."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from minty_core.system.runner import Result
from tests.plugin_helpers import make_host
from tests.test_integrations_home_alerts import STATES, FakeHA, Player, hourly, history
from tests.test_integrations_ics import SAMPLE
from tests.test_integrations_lan_n8n import NEIGH, Net

IST = ZoneInfo("Asia/Kolkata")
FEED = "https://calendar.example/private/abcd1234/basic.ics"


class Rig:
    def __init__(self, tmp_path, now=datetime(2026, 10, 19, 10, 0, tzinfo=IST), **integ):
        self.ha = FakeHA(history(("sensor.geyser_energy", [1210.5, 1213.7])))
        self.player, self.net = Player(), Net()
        self.fetched, self.forecast = [], hourly(codes={15: 95})

        async def fetch(url):
            self.fetched.append(url)
            return SAMPLE

        async def get_json(url, params=None):
            if "geocoding" in url:
                return {"results": [{"name": "Paris", "latitude": 30.7, "longitude": 76.7}]}
            return self.forecast

        async def nap(_s):
            return None

        self.host = make_host(tmp_path / "cfg", services={
            "integrations_fetch": fetch, "integrations_ha": self.ha.request, "integrations_get": get_json,
            "integrations_player": self.player, "integrations_lan": self.net, "integrations_sleep": nap})
        data = self.host._store.config.model_dump(mode="json")
        data["integrations"].update({"enabled": True, "calendar": True, "home": True, "sleep_timer": True, "weather_alerts": True,
                                     "lan": True, "lan_ranges": ["192.168.1.0/29"], "location": "Paris", "tariff": 8.0, **integ})
        self.host._store.update(data)
        self.host._services["integrations"]._clock = lambda: now.timestamp()
        self.tmp = tmp_path

    async def call(self, tool, args):
        return await self.host.call(f"integrations_{tool}", args)


async def test_everything_is_off_until_switched_on(tmp_path):
    r = Rig(tmp_path, enabled=False)
    for tool, args in (("calendar", {}), ("home", {}), ("sleep", {}), ("weather", {}), ("lan", {}), ("n8n", {})):
        assert "integrations.enabled" in await r.call(tool, args), tool
    for part, tool in (("calendar", "calendar"), ("home", "home"), ("sleep_timer", "sleep"), ("weather_alerts", "weather"),
                       ("lan", "lan")):
        off = Rig(tmp_path / part, **{part: False})
        assert f"integrations.{part}" in await off.call(tool, {})


async def test_the_calendar_is_added_read_never_echoed_back_and_answers_in_local_time(tmp_path):
    r = Rig(tmp_path)
    added = await r.call("calendar", {"action": "add_feed", "name": "school", "url": FEED})
    assert "4 events" in added and "abcd1234" not in added and r.fetched == [FEED]
    assert "abcd1234" not in await r.call("calendar", {"action": "feeds"})
    today = await r.call("calendar", {"action": "agenda"})
    assert today.startswith("[untrusted calendar text") and "08:00 Maths, Room 4" in today and "15:00 Call with" in today
    assert "Next: Call with a very long folded line here at 15:00 today" in await r.call("calendar", {"action": "next"})
    assert "Dussehra" in await r.call("calendar", {"action": "agenda", "day": "tomorrow"})
    week = await r.call("calendar", {"action": "week"})
    assert week.count("\n") == 7 and "Monday 19 Oct" in week and "Sunday 25 Oct" in week
    assert "Free on Monday 19 Oct" in await r.call("calendar", {"action": "free", "minutes": 60})
    assert "Say today, tomorrow" in await r.call("calendar", {"action": "agenda", "day": "someday maybe"})
    again = await r.call("calendar", {"action": "refresh"})
    assert "0 new, 0 changed, 0 removed" in again  # a re-import changes nothing
    assert "http://calendar.example/x.ics" not in str(r.fetched)
    assert "https" in await r.call("calendar", {"action": "add_feed", "name": "bad", "url": "http://calendar.example/x.ics"})


async def test_home_scenes_and_energy(tmp_path):
    r = Rig(tmp_path)
    assert "Movie Night is on." == await r.call("home", {"action": "scene", "name": "movie night"})
    assert any(c[:2] == ("POST", "/api/services/scene/turn_on") for c in r.ha.calls)
    assert "Movie Night" in await r.call("home", {"action": "scenes"})
    out = await r.call("home", {"action": "energy"})
    assert "3.2 kWh today" in out and "₹26" in out
    assert r.host.get_tool("integrations_home").risk({"action": "scene"}) == 1
    assert r.host.get_tool("integrations_home").risk({"action": "energy"}) == 0


async def test_the_sleep_timer_tool(tmp_path):
    r = Rig(tmp_path)
    assert "No sleep timer" in await r.call("sleep", {"action": "status"})
    assert "30 minutes" in await r.call("sleep", {"action": "start", "minutes": 30, "fade": 2})
    assert "left" in await r.call("sleep", {"action": "status"})
    assert "already running" in await r.call("sleep", {"action": "start", "minutes": 10})
    assert "Cancelled" in await r.call("sleep", {"action": "cancel"})
    assert "1 to 480" in await r.call("sleep", {"action": "start", "minutes": 0})


async def test_weather_alerts_are_labelled_as_forecast_derived(tmp_path):
    r = Rig(tmp_path)
    out = await r.call("weather", {})
    assert out.startswith("[untrusted") and "not an official warning" in out and "Thunderstorm" in out
    r.forecast = hourly()
    assert "nothing severe" in await r.call("weather", {"place": "Paris"})
    assert "Which place" in await Rig(tmp_path / "noplace", location="").call("weather", {})


async def test_the_network_scan_asks_first_and_stays_on_the_listed_private_range(tmp_path):
    r = Rig(tmp_path)
    scan = r.host.get_tool("integrations_lan")
    assert scan.risk({"action": "scan"}) == 2 and scan.risk({"action": "name"}) == 1
    out = await r.call("lan", {"action": "scan"})
    assert "Found 1 device" in out and "192.168.1.1" in out
    pinged = sorted(c[-1] for c in r.net.calls if c[0] == "ping")
    assert pinged and all(ip.startswith("192.168.1.") and 1 <= int(ip.rsplit(".", 1)[1]) <= 6 for ip in pinged)
    assert "too soon" in await r.call("lan", {"action": "scan"})
    assert "Saved." == await r.call("lan", {"action": "name", "mac": "aa:bb:cc:00:00:01", "label": "Router"})
    wide = Rig(tmp_path / "wide", lan_ranges=["8.8.8.0/24"])
    assert "not a private home-network range" in await wide.call("lan", {"action": "scan"})
    assert wide.net.calls == []


async def test_n8n_templates_are_written_as_inactive_json_files(tmp_path):
    r = Rig(tmp_path)
    assert "rss-to-telegram" in await r.call("n8n", {"action": "templates"})
    out = await r.call("n8n", {"action": "make", "template": "rss-to-telegram", "chat_id": "123456789",
                               "url": "https://feeds.example/rss"})
    path = tmp_path / "cfg" / "n8n-templates" / "rss-to-telegram.json"
    assert str(path) in out and json.loads(path.read_text())["active"] is False and "could not test" in out
    assert "no template" in (await r.call("n8n", {"action": "make", "template": "format-disk", "chat_id": "123456"})).lower()
    assert "Telegram chat id" in await r.call("n8n", {"action": "make", "template": "rss-to-telegram", "chat_id": "x",
                                                      "url": "https://feeds.example/rss"})


async def test_the_loop_refreshes_calendars_hourly_and_reports_weather_once(tmp_path):
    r = Rig(tmp_path)
    await r.call("calendar", {"action": "add_feed", "name": "school", "url": FEED})
    p = r.host._services["integrations"]
    t = [datetime(2026, 10, 19, 10, 0, tzinfo=IST).timestamp()]
    p._clock = lambda: t[0]
    first = await p.tick()
    assert len(first) == 1 and "Thunderstorm" in first[0] and len(r.fetched) == 2  # the add, then the hourly refresh
    t[0] += 600
    assert await p.tick() == [] and len(r.fetched) == 2  # neither is due again yet
    t[0] += 3 * 3600
    assert await p.tick() == [] and len(r.fetched) == 3  # calendar refreshed; the same alert is not repeated
