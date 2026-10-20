"""Short-lived answer cache for idempotent lookups (W0-21)."""
import time

import pytest

from tests.plugin_helpers import make_host
from minty_core.answer_cache import AnswerCache, personal
from minty_core.plugins.host import Tool
from minty_core.trace import annotate_turn, turn_scope


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_hit_within_ttl_and_miss_after():
    clock = Clock()
    cache = AnswerCache(clock=clock)
    cache.put("weather_get_weather", {"city": "Delhi"}, "31 °C, sunny", ttl=600)
    assert cache.get("weather_get_weather", {"city": " delhi "}) == "31 °C, sunny"  # same question, other spelling
    clock.now += 601
    assert cache.get("weather_get_weather", {"city": "Delhi"}) is None


def test_a_hit_is_fast():
    cache = AnswerCache()
    cache.put("weather_get_weather", {"city": "Delhi"}, "x" * 2000, ttl=600)
    start = time.perf_counter()
    for _ in range(1000):
        cache.get("weather_get_weather", {"city": "Delhi"})
    assert (time.perf_counter() - start) / 1000 < 0.005


@pytest.mark.parametrize("args", [
    {"query": "alex@example.com"}, {"query": "call +91 98765 43210"}, {"query": "account 123456789012"},
    {"query": "my exam results"}, {"query": "what did my teacher say"},
])
def test_personal_looking_lookups_are_never_cached(args):
    assert personal(args)
    cache = AnswerCache()
    cache.put("web_search_search", args, "result", ttl=600)
    assert cache.get("web_search_search", args) is None


def test_nothing_is_cached_in_a_private_turn():
    cache = AnswerCache()
    with turn_scope():
        annotate_turn(private=True)
        cache.put("weather_get_weather", {"city": "Delhi"}, "31", ttl=600)
    assert cache.get("weather_get_weather", {"city": "Delhi"}) is None


def test_size_is_bounded():
    cache = AnswerCache(max_items=3)
    for i in range(5):
        cache.put("t", {"q": str(i)}, str(i), ttl=600)
    assert cache.get("t", {"q": "0"}) is None and cache.get("t", {"q": "4"}) == "4"


async def test_host_serves_repeated_weather_from_the_cache(tmp_path):
    calls = []

    def weather(args):
        calls.append(args)
        return f"Weather in {args['city']}: 31 °C"

    host = make_host(tmp_path)
    host._services["answer_cache"] = AnswerCache()
    host._plugins["weather"].tools["weather_get_weather"] = Tool(
        "weather_get_weather", "w", {}, weather, cache_ttl=600)
    first = await host.call("weather_get_weather", {"city": "Delhi"})
    second = await host.call("weather_get_weather", {"city": "Delhi"})
    assert first == second and len(calls) == 1


async def test_errors_and_uncacheable_tools_are_not_cached(tmp_path):
    calls = []

    def flaky(args):
        calls.append(1)
        return "Error: service down"

    host = make_host(tmp_path)
    host._services["answer_cache"] = AnswerCache()
    host._plugins["weather"].tools["weather_get_weather"] = Tool("weather_get_weather", "w", {}, flaky, cache_ttl=600)
    await host.call("weather_get_weather", {"city": "Delhi"})
    await host.call("weather_get_weather", {"city": "Delhi"})
    assert len(calls) == 2


def test_weather_and_web_search_declare_their_ttl(tmp_path):
    host = make_host(tmp_path, plugins={"weather": {"enabled": True}, "web_search": {"enabled": True}})
    assert host.get_tool("weather_get_weather").cache_ttl == 600
    assert host.get_tool("web_search_search").cache_ttl == 3600
