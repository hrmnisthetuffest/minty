"""Which hosts each plugin has contacted (W1-20)."""
import httpx

from tests.plugin_helpers import make_host
from minty_core import destinations
from minty_core.commands import CommandRouter
from minty_core.destinations import DestinationMap, hooks, plugin_scope, set_registry
from minty_core.plugins.host import Tool, _Loaded


def client():
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")),
                             event_hooks=hooks())


async def test_requests_are_attributed_to_the_running_plugin_and_only_hosts_are_kept(tmp_path):
    m = DestinationMap(tmp_path / "d.sqlite")
    set_registry(m)
    try:
        async with client() as c:
            with plugin_scope("weather"):
                await c.get("https://api.weather.example/v1/forecast?lat=30.7&lon=76.7&key=SECRET")
                await c.get("https://api.weather.example/v1/forecast")
            with plugin_scope("browser"):
                await c.get("https://en.wikipedia.org/wiki/Taekwondo")
            await c.get("https://api.groq.com/openai/v1/models")  # outside any plugin
    finally:
        set_registry(None)
    assert m.listing() == {"browser": [("en.wikipedia.org", 1)], "core": [("api.groq.com", 1)],
                           "weather": [("api.weather.example", 2)]}
    assert "SECRET" not in str(m.listing())
    assert m.summary("weather") == "weather: api.weather.example."
    assert "nothing" in DestinationMap(tmp_path / "e.sqlite").summary().lower()


async def test_the_plugin_host_sets_the_scope_for_a_tool_call(tmp_path):
    m = DestinationMap(tmp_path / "d.sqlite")
    set_registry(m)

    async def fetch(args):
        async with client() as c:
            await c.get("https://api.example.org/data")
        return "done"

    host = make_host(tmp_path / "cfg")
    host._plugins["t"] = _Loaded(name="t", enabled=True, tools={"t_fetch": Tool("t_fetch", "x", {"type": "object"}, fetch)})
    try:
        await host.call("t_fetch", {})
    finally:
        set_registry(None)
    assert m.listing() == {"t": [("api.example.org", 1)]}


def test_by_voice(tmp_path):
    m = DestinationMap(tmp_path / "d.sqlite")
    m.record("weather", "api.weather.example")
    router = CommandRouter(agent=None, destinations=m.summary)
    assert router.handle("where did the plugins connect") == "weather: api.weather.example."
    assert router.handle("which sites did the weather contact") == "weather: api.weather.example."
    assert destinations.current_plugin() == "core"
