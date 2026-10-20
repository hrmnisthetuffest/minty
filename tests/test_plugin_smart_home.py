import json

import httpx
import pytest

from tests.plugin_helpers import make_host, mock_http

STATES = [
    {"entity_id": "light.living_room", "state": "on", "attributes": {"friendly_name": "Living Room Light", "brightness": 128}},
    {"entity_id": "switch.fan", "state": "off", "attributes": {"friendly_name": "Fan"}},
    {"entity_id": "lock.front_door", "state": "locked", "attributes": {"friendly_name": "Front Door"}},
    {"entity_id": "scene.movie", "state": "scening", "attributes": {"friendly_name": "Movie Night"}},
    {"entity_id": "sensor.hall_temp", "state": "19.5", "attributes": {"friendly_name": "Hall Temp", "unit_of_measurement": "°C"}},
]
CONFIG = {"smart_home": {"enabled": True, "url": "http://ha.test:8123"}}
SECRETS = {"plugin.smart_home.token": "TOKEN-123456"}


def ha(monkeypatch, status=200):
    calls = []

    def handler(req):
        calls.append((req.method, req.url.path, json.loads(req.content) if req.content else None, req.headers.get("authorization")))
        if status != 200:
            return httpx.Response(status)
        if req.method == "GET" and req.url.path == "/api/states":
            return httpx.Response(200, json=STATES)
        if req.method == "GET" and req.url.path.startswith("/api/states/"):
            eid = req.url.path.rsplit("/", 1)[1]
            return httpx.Response(200, json=next(s for s in STATES if s["entity_id"] == eid))
        return httpx.Response(200, json=[])

    mock_http(monkeypatch, handler)
    return calls


def host(tmp_path):
    return make_host(tmp_path, plugins=CONFIG, secrets=SECRETS)


async def test_disabled_by_default_and_unconfigured_message(tmp_path):
    assert make_host(tmp_path).get_tool("smart_home_list_devices") is None
    unconfigured = make_host(tmp_path, plugins={"smart_home": {"enabled": True}})
    assert "isn't configured" in await unconfigured.call("smart_home_list_devices", {})


async def test_list_devices_defaults_to_controllable_domains(tmp_path, monkeypatch):
    calls = ha(monkeypatch)
    out = await host(tmp_path).call("smart_home_list_devices", {})
    assert "light.living_room: Living Room Light (on)" in out and "lock.front_door: Front Door (locked)" in out
    assert "sensor.hall_temp" not in out
    assert calls[0][3] == "Bearer TOKEN-123456"
    assert "sensor.hall_temp: Hall Temp (19.5)" in await host(tmp_path).call("smart_home_list_devices", {"domain": "sensor"})


async def test_get_state_includes_unit(tmp_path, monkeypatch):
    ha(monkeypatch)
    assert await host(tmp_path).call("smart_home_get_state", {"entity_id": "sensor.hall_temp"}) == "Hall Temp: 19.5 °C"


async def test_set_device_and_brightness_and_scene_call_the_right_services(tmp_path, monkeypatch):
    calls = ha(monkeypatch)
    h = host(tmp_path)
    await h.call("smart_home_set_device", {"entity_id": "switch.fan", "on": True})
    await h.call("smart_home_set_device", {"entity_id": "light.living_room", "on": False})
    await h.call("smart_home_set_brightness", {"entity_id": "light.living_room", "percent": 40})
    await h.call("smart_home_run_scene", {"entity_id": "scene.movie"})
    posts = [(p, b) for m, p, b, _ in calls if m == "POST"]
    assert posts == [
        ("/api/services/switch/turn_on", {"entity_id": "switch.fan"}),
        ("/api/services/light/turn_off", {"entity_id": "light.living_room"}),
        ("/api/services/light/turn_on", {"entity_id": "light.living_room", "brightness_pct": 40}),
        ("/api/services/scene/turn_on", {"entity_id": "scene.movie"}),
    ]


@pytest.mark.parametrize("entity", ["lock.front_door", "cover.garage_door", "alarm_control_panel.home"])
async def test_set_device_refuses_secure_domains_without_calling_ha(tmp_path, monkeypatch, entity):  # RF8
    calls = ha(monkeypatch)
    out = await host(tmp_path).call("smart_home_set_device", {"entity_id": entity, "on": True})
    assert "control_secure" in out or "can't" in out
    assert [c for c in calls if c[0] == "POST"] == []


async def test_control_secure_is_risky_and_limited(tmp_path, monkeypatch):  # RF8
    calls = ha(monkeypatch)
    h = host(tmp_path)
    assert h.get_tool("smart_home_control_secure").risky is True
    await h.call("smart_home_control_secure", {"entity_id": "lock.front_door", "action": "unlock"})
    await h.call("smart_home_control_secure", {"entity_id": "cover.garage_door", "action": "close"})
    assert [(p, b) for m, p, b, _ in calls if m == "POST"] == [
        ("/api/services/lock/unlock", {"entity_id": "lock.front_door"}),
        ("/api/services/cover/close_cover", {"entity_id": "cover.garage_door"}),
    ]
    calls.clear()
    assert "can't" in await h.call("smart_home_control_secure", {"entity_id": "alarm_control_panel.home", "action": "disarm"})
    assert "can't" in await h.call("smart_home_control_secure", {"entity_id": "lock.front_door", "action": "explode"})
    assert calls == []


@pytest.mark.parametrize("entity", ["light.x/../../api/config", "nonsense", "LIGHT.x", "light.a b", "light.x\n"])
async def test_entity_ids_are_validated(tmp_path, monkeypatch, entity):
    calls = ha(monkeypatch)
    out = await host(tmp_path).call("smart_home_set_device", {"entity_id": entity, "on": True})
    assert out.startswith("Error: ValueError") and calls == []


async def test_brightness_range_and_domain(tmp_path, monkeypatch):
    ha(monkeypatch)
    h = host(tmp_path)
    assert (await h.call("smart_home_set_brightness", {"entity_id": "light.living_room", "percent": 150})).startswith("Error: ValueError")
    assert (await h.call("smart_home_set_brightness", {"entity_id": "switch.fan", "percent": 50})).startswith("Error: ValueError")


async def test_rejected_token_gives_clear_error(tmp_path, monkeypatch):
    ha(monkeypatch, status=401)
    out = await host(tmp_path).call("smart_home_list_devices", {})
    assert "rejected the token" in out


@pytest.mark.parametrize("entity", ["script.unlock_all", "automation.x", "valve.main"])
async def test_set_device_only_switches_allowlisted_domains(tmp_path, monkeypatch, entity):
    calls = ha(monkeypatch)
    out = await host(tmp_path).call("smart_home_set_device", {"entity_id": entity, "on": True})
    assert "can't switch" in out
    assert calls == []


async def test_set_device_requires_real_bool(tmp_path, monkeypatch):
    calls = ha(monkeypatch)
    out = await host(tmp_path).call("smart_home_set_device", {"entity_id": "switch.fan", "on": "false"})
    assert out.startswith("Error: ValueError") and calls == []


async def test_control_secure_confirmation_names_the_action(tmp_path):
    tool = host(tmp_path).get_tool("smart_home_control_secure")
    assert tool.describe({"entity_id": "lock.front_door", "action": "unlock"}) == "Should I unlock lock.front_door?"
    assert tool.describe({"entity_id": "cover.garage_door", "action": "open"}) == "Should I open cover.garage_door?"
