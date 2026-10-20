import pytest

from minty_core.config import ConfigStore
from minty_core.keystore import SecretsStore
from minty_core.llm.router import ProviderRouter
from minty_core.llm.types import LLMError, Message, TextDelta, ToolSpec


class FakeProvider:
    def __init__(self, name, script):
        self.name = name
        self.script = script

    async def stream(self, messages, tools):
        for item in self.script:
            if isinstance(item, Exception):
                raise item
            yield item


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


def P(name, **kw):
    return {"name": name, "model": "m", **kw}


def make_router(tmp_path, providers, keys, scripts, clock=None):
    store = ConfigStore(tmp_path)
    store.load()
    data = store.config.model_dump(mode="json")
    data["providers"] = providers
    store.update(data)
    secrets = SecretsStore(tmp_path)
    for k in keys:
        secrets.set(k, "key-" + k)
    calls = []
    keys_seen = {}

    def factory(cfg, key):
        calls.append(cfg.name)
        keys_seen[cfg.name] = key
        return FakeProvider(cfg.name, scripts[cfg.name])

    router = ProviderRouter(store, secrets, factory, clock or Clock())
    router.keys_seen = keys_seen
    return router, calls


async def collect(router, tools=(), sensitive=False):
    return [e async for e in router.stream([Message("user", "hi")], list(tools), sensitive=sensitive)]


async def test_failover_cooldown_and_recovery(tmp_path):
    clock = Clock()
    router, calls = make_router(
        tmp_path, [P("a"), P("b")], ["a", "b"],
        {"a": [LLMError("rate limited", status=429)], "b": [TextDelta("ok")]}, clock,
    )
    assert await collect(router) == [TextDelta("ok")]
    assert calls == ["a", "b"]
    calls.clear()
    await collect(router)
    assert calls == ["b"]
    clock.now += 61
    calls.clear()
    await collect(router)
    assert calls == ["a", "b"]


async def test_no_failover_after_partial_output_but_provider_cools(tmp_path):
    router, calls = make_router(
        tmp_path, [P("a"), P("b")], ["a", "b"],
        {"a": [TextDelta("hi"), LLMError("died", status=500)], "b": [TextDelta("ok")]},
    )
    with pytest.raises(LLMError):
        await collect(router)
    assert calls == ["a"]
    assert next(s for s in router.status() if s["name"] == "a")["cooling_for"] > 0


async def test_skip_rules(tmp_path):
    scripts = {n: [TextDelta(n)] for n in ["nokey", "notools", "private", "ok"]}
    router, calls = make_router(
        tmp_path,
        [P("nokey"), P("notools", supports_tools=False), P("private", allow_sensitive=False), P("ok")],
        ["notools", "private", "ok"], scripts,
    )
    tool = ToolSpec("t", "d", {"type": "object", "properties": {}})
    assert await collect(router, [tool]) == [TextDelta("private")]
    assert await collect(router, [tool], sensitive=True) == [TextDelta("ok")]
    assert calls == ["private", "ok"]


async def test_keyless_provider_allowed_when_not_required(tmp_path):
    router, calls = make_router(tmp_path, [P("ollama", requires_key=False)], [], {"ollama": [TextDelta("local")]})
    assert await collect(router) == [TextDelta("local")]


async def test_all_providers_failing_raises(tmp_path):
    router, _ = make_router(tmp_path, [P("a")], ["a"], {"a": [LLMError("x", status=500)]})
    with pytest.raises(LLMError, match="no LLM provider"):
        await collect(router)
    with pytest.raises(LLMError, match="no LLM provider"):
        await collect(router)


async def test_bad_key_gets_long_cooldown_and_retry_after_is_honoured(tmp_path):
    router, _ = make_router(
        tmp_path, [P("a"), P("b")], ["a", "b"],
        {"a": [LLMError("bad key", status=401)], "b": [LLMError("slow", status=429, retry_after=5)]},
    )
    with pytest.raises(LLMError):
        await collect(router)
    status = {s["name"]: s for s in router.status()}
    assert status["a"]["cooling_for"] == 3600
    assert status["b"]["cooling_for"] == 5
    assert status["a"]["last_error"].startswith("bad key")


async def test_requested_tier_goes_first_then_falls_back(tmp_path):
    scripts = {"fast1": [TextDelta("fast")], "deep1": [TextDelta("deep")]}
    router, calls = make_router(tmp_path, [P("fast1"), P("deep1", tier="deep")], ["fast1", "deep1"], scripts)
    assert await collect(router) == [TextDelta("fast")]
    assert [e async for e in router.stream([Message("user", "q")], [], tier="deep")] == [TextDelta("deep")]
    assert calls == ["fast1", "deep1"]


async def test_deep_request_falls_back_to_fast_when_no_deep_provider_works(tmp_path):
    scripts = {"fast1": [TextDelta("fast")], "deep1": [LLMError("down", status=500)]}
    router, calls = make_router(tmp_path, [P("fast1"), P("deep1", tier="deep")], ["fast1", "deep1"], scripts)
    assert [e async for e in router.stream([Message("user", "q")], [], tier="deep")] == [TextDelta("fast")]
    assert calls == ["deep1", "fast1"]


async def test_key_name_shares_a_secret(tmp_path):
    router, calls = make_router(tmp_path, [P("nvidia-deep", key_name="nvidia")], ["nvidia"],
                                {"nvidia-deep": [TextDelta("ok")]})
    assert await collect(router) == [TextDelta("ok")]
    status = router.status()[0]
    assert status["has_key"] is True and status["tier"] == "fast"


async def test_tier_does_not_loosen_the_skip_rules(tmp_path):
    scripts = {n: [TextDelta(n)] for n in ["deep-open", "deep-nokey", "deep-notools", "fast-ok"]}
    router, calls = make_router(
        tmp_path,
        [P("deep-open", tier="deep", allow_sensitive=False), P("deep-nokey", tier="deep", key_name="missing"),
         P("deep-notools", tier="deep", supports_tools=False), P("fast-ok")],
        ["deep-open", "deep-notools", "fast-ok"], scripts,
    )
    tool = ToolSpec("t", "d", {"type": "object", "properties": {}})

    async def ask(tools, sensitive):
        return [e async for e in router.stream([Message("user", "q")], tools, sensitive=sensitive, tier="deep")]

    assert await ask([], True) == [TextDelta("deep-notools")]     # private: allow_sensitive=False skipped
    assert await ask([tool], False) == [TextDelta("deep-open")]   # no tools support skipped, missing key skipped
    assert calls == ["deep-notools", "deep-open"]


async def test_missing_shared_key_skips_provider(tmp_path):
    router, calls = make_router(tmp_path, [P("nvidia-deep", key_name="nvidia"), P("ok")], ["nvidia-deep", "ok"],
                                {"nvidia-deep": [TextDelta("deep")], "ok": [TextDelta("ok")]})
    assert await collect(router) == [TextDelta("ok")]
    assert calls == ["ok"]
    assert router.status()[0]["has_key"] is False


async def test_factory_receives_the_shared_key_value(tmp_path):
    router, _ = make_router(tmp_path, [P("nvidia-deep", key_name="nvidia", tier="deep")], ["nvidia"],
                            {"nvidia-deep": [TextDelta("ok")]})
    assert [e async for e in router.stream([Message("user", "q")], [], tier="deep")] == [TextDelta("ok")]
    assert router.keys_seen == {"nvidia-deep": "key-nvidia"}


class RecordingTracer:
    def __init__(self):
        self.events = []

    def event(self, kind, **data):
        self.events.append({"kind": kind, **data})


async def test_every_attempt_is_traced_with_timing_and_outcome(tmp_path):
    router, _ = make_router(
        tmp_path, [P("a"), P("b")], ["a", "b"],
        {"a": [LLMError("a: HTTP 429", status=429)], "b": [TextDelta("hel"), TextDelta("lo")]})
    router.tracer = RecordingTracer()
    assert [e.text async for e in router.stream([Message("user", "hi there")], [])] == ["hel", "lo"]
    failed, ok = router.tracer.events
    assert failed["provider"] == "a" and failed["outcome"] == "error" and failed["status"] == 429
    assert ok["provider"] == "b" and ok["outcome"] == "ok" and ok["chars_out"] == 5 and ok["chars_in"] == 8
    assert ok["first_token_ms"] is not None and ok["total_ms"] >= ok["first_token_ms"]
    assert failed["first_token_ms"] is None


async def test_a_consumer_that_stops_early_is_traced_as_cancelled(tmp_path):
    router, _ = make_router(tmp_path, [P("a")], ["a"], {"a": [TextDelta("one"), TextDelta("two")]})
    router.tracer = RecordingTracer()
    stream = router.stream([Message("user", "hi")], [])
    await stream.__anext__()
    await stream.aclose()
    [event] = router.tracer.events
    assert event["outcome"] == "cancelled" and event["chars_out"] == 3


async def test_a_provider_that_keeps_failing_rests_longer_each_time_and_success_resets_it(tmp_path):
    clock = Clock()
    script = {"a": [LLMError("rate limited", status=429)], "b": [TextDelta("ok")]}
    router, calls = make_router(tmp_path, [P("a"), P("b")], ["a", "b"], script, clock)
    data = router._store.config.model_dump(mode="json")
    data["router"]["health_order"] = False  # this test is about the rests; health would move a to the back
    router._store.update(data)
    rests = []
    for _ in range(5):
        calls.clear()
        await collect(router)
        assert calls[0] == "a"
        rests.append(router.status()[0]["cooling_for"])
        clock.now += rests[-1] + 1
    assert rests == [60.0, 120.0, 240.0, 480.0, 900.0]  # doubles, capped at 15 minutes
    script["a"] = [TextDelta("back")]
    assert await collect(router) == [TextDelta("back")]
    script["a"] = [LLMError("rate limited", status=429)]
    await collect(router)
    assert router.status()[0]["cooling_for"] == 60.0  # one success clears the streak
