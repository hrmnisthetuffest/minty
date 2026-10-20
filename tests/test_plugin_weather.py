import httpx

from tests.plugin_helpers import make_host, mock_http

FORECAST = {
    "current": {"temperature_2m": 12.3, "apparent_temperature": 10.1, "weather_code": 61, "wind_speed_10m": 14.0},
    "current_units": {"temperature_2m": "°C", "wind_speed_10m": "km/h"},
    "daily": {"temperature_2m_max": [14.2], "temperature_2m_min": [8.4], "precipitation_probability_max": [60]},
}


def handler_factory(seen, found=True):
    def handler(req):
        seen.append(req)
        if req.url.host == "geocoding-api.open-meteo.com":
            results = [{"name": "Leeds", "latitude": 53.8, "longitude": -1.55}] if found else None
            return httpx.Response(200, json={"results": results} if results else {})
        return httpx.Response(200, json=FORECAST)

    return handler


async def test_explicit_location(tmp_path, monkeypatch):
    seen = []
    mock_http(monkeypatch, handler_factory(seen))
    host = make_host(tmp_path)
    out = await host.call("weather_get_weather", {"location": "Leeds"})
    assert out == "Leeds: 12°C (feels like 10°C), light rain, wind 14 km/h. Today: high 14, low 8, 60% chance of rain."
    assert seen[0].url.params["name"] == "Leeds"
    assert seen[1].url.params["latitude"] == "53.8"


async def test_default_location_from_settings_and_imperial_units(tmp_path, monkeypatch):
    seen = []
    mock_http(monkeypatch, handler_factory(seen))
    host = make_host(tmp_path, plugins={"weather": {"location": "Leeds", "units": "imperial"}})
    await host.call("weather_get_weather", {})
    assert seen[0].url.params["name"] == "Leeds"
    assert seen[1].url.params["temperature_unit"] == "fahrenheit"
    assert seen[1].url.params["wind_speed_unit"] == "mph"


async def test_no_location_anywhere(tmp_path, monkeypatch):
    mock_http(monkeypatch, handler_factory([]))
    out = await make_host(tmp_path).call("weather_get_weather", {})
    assert "location" in out.lower() and not out.startswith("Error")


async def test_unknown_place(tmp_path, monkeypatch):
    mock_http(monkeypatch, handler_factory([], found=False))
    out = await make_host(tmp_path).call("weather_get_weather", {"location": "Atlantis"})
    assert out == "I couldn't find a place called Atlantis."


async def test_http_failure_becomes_error_string(tmp_path, monkeypatch):
    mock_http(monkeypatch, lambda req: httpx.Response(500))
    out = await make_host(tmp_path).call("weather_get_weather", {"location": "Leeds"})
    assert out.startswith("Error:")
