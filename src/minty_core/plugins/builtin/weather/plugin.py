from minty_core.httpclient import get_client
from minty_core.plugins.host import Tool

GEO = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
CODES = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 80: "rain showers", 81: "rain showers",
    82: "violent rain showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail",
}


def register(ctx):
    async def get_weather(args):
        place = str(args.get("location") or ctx.settings.get("location") or "").strip()
        if not place:
            return "I don't have a location. Tell me a city, or set a default location in the panel."
        client = get_client()
        geo = await client.get(GEO, params={"name": place, "count": 1})
        geo.raise_for_status()
        results = geo.json().get("results") or []
        if not results:
            return f"I couldn't find a place called {place}."
        found = results[0]
        params = {
            "latitude": found["latitude"], "longitude": found["longitude"],
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "auto", "forecast_days": 1,
        }
        if ctx.settings.get("units") == "imperial":
            params.update(temperature_unit="fahrenheit", wind_speed_unit="mph")
        resp = await client.get(FORECAST, params=params)
        resp.raise_for_status()
        data = resp.json()
        cur, units, day = data["current"], data["current_units"], data["daily"]
        t_unit, w_unit = units["temperature_2m"], units["wind_speed_10m"]
        sky = CODES.get(cur["weather_code"], "unsettled weather")
        return (
            f"{found['name']}: {round(cur['temperature_2m'])}{t_unit} "
            f"(feels like {round(cur['apparent_temperature'])}{t_unit}), {sky}, "
            f"wind {round(cur['wind_speed_10m'])} {w_unit}. "
            f"Today: high {round(day['temperature_2m_max'][0])}, low {round(day['temperature_2m_min'][0])}, "
            f"{round(day['precipitation_probability_max'][0])}% chance of rain."
        )

    schema = {"type": "object", "properties": {"location": {"type": "string"}}}
    return [Tool("get_weather", "Get the current weather and today's forecast for a city.", schema, get_weather,
                 cache_ttl=600)]
