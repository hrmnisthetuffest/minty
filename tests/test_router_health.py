"""Health-ordered routing (W0-06): dead providers sink, deep models stay out of fast turns."""
import pytest

from tests.test_router import Clock
from minty_core.config import ConfigStore
from minty_core.httpclient import error_from_response
from minty_core.keystore import SecretsStore
from minty_core.llm.router import DEMOTE_S, ProviderRouter
from minty_core.llm.types import LLMError, Message, TextDelta

OK = [TextDelta("ok")]


class Queue:
    def __init__(self, name, items):
        self.name, self.items = name, items

    async def stream(self, messages, tools):
        item = self.items.pop(0) if len(self.items) > 1 else self.items[0]
        if isinstance(item, Exception):
            raise item
        for ev in item:
            yield ev


def make(tmp_path, providers, scripts, clock=None, **router_cfg):
    store = ConfigStore(tmp_path)
    data = store.load().model_dump(mode="json")
    data["providers"] = [{"name": n, "model": "m", **kw} for n, kw in providers]
    data["router"].update(router_cfg)
    store.update(data)
    secrets = SecretsStore(tmp_path)
    for n, _ in providers:
        secrets.set(n, "key-" + n)
    calls = []

    def factory(cfg, key):
        calls.append(cfg.name)
        return Queue(cfg.name, scripts[cfg.name])

    return ProviderRouter(store, secrets, factory, clock or Clock()), calls


async def ask(router, tier="fast"):
    return [e async for e in router.stream([Message("user", "hi")], [], tier=tier)]


def fail(status=500):
    return LLMError("boom", status=status)


async def demote_a(router, clock):
    for _ in range(3):
        await ask(router)
        clock.now += 400  # past any cooldown, so only health decides the order


async def test_provider_with_no_recent_success_is_demoted(tmp_path):
    clock = Clock()
    router, calls = make(tmp_path, [("a", {}), ("b", {})], {"a": [fail()], "b": [OK]}, clock)
    await demote_a(router, clock)
    calls.clear()
    await ask(router)
    assert calls == ["b"]  # a is last in line now, so b answers first
    row = next(r for r in router.status() if r["name"] == "a")
    assert row["health"]["ok"] == 0 and row["health"]["calls"] == 3 and row["health"]["demoted_for"] > 0


async def test_demotion_is_time_boxed_and_a_probe_restores_it(tmp_path):
    clock = Clock()
    router, calls = make(tmp_path, [("a", {}), ("b", {})], {"a": [fail(), fail(), fail(), OK], "b": [OK]}, clock)
    await demote_a(router, clock)
    clock.now += DEMOTE_S
    calls.clear()
    await ask(router)
    assert calls == ["a"]  # probed first again; it answers
    calls.clear()
    await ask(router)
    assert calls == ["a"]


async def test_mostly_failing_provider_sinks_below_healthy_ones(tmp_path):
    clock = Clock()
    router, calls = make(tmp_path, [("a", {}), ("b", {})],
                         {"a": [OK, fail(), fail(), fail(), OK], "b": [OK]}, clock)
    for _ in range(4):
        await ask(router)
        clock.now += 400
    calls.clear()
    await ask(router)
    assert calls[0] == "b"


async def test_deep_models_never_join_a_fast_chain(tmp_path):
    router, calls = make(tmp_path, [("a", {}), ("d", {"tier": "deep"})], {"a": [fail()], "d": [OK]})
    try:
        await ask(router)
    except LLMError:
        pass
    assert calls == ["a"]


async def test_deep_fallback_can_be_allowed(tmp_path):
    router, calls = make(tmp_path, [("a", {}), ("d", {"tier": "deep"})], {"a": [fail()], "d": [OK]},
                         allow_deep_fallback=True)
    assert await ask(router) == OK and calls == ["a", "d"]


async def test_thinking_mode_still_falls_back_to_fast_models(tmp_path):
    router, calls = make(tmp_path, [("a", {}), ("d", {"tier": "deep"})], {"a": [OK], "d": [fail()]})
    assert await ask(router, tier="deep") == OK and calls == ["d", "a"]


async def test_preferred_provider_leads_unless_demoted(tmp_path):
    clock = Clock()
    router, calls = make(tmp_path, [("a", {}), ("b", {})], {"a": [OK], "b": [fail()]}, clock)
    data = router._store.config.model_dump(mode="json")
    data["preferred_provider"] = "b"
    router._store.update(data)
    await ask(router)
    assert calls[0] == "b"
    for _ in range(2):
        clock.now += 400
        await ask(router)
    clock.now += 400
    calls.clear()
    await ask(router)
    assert calls == ["a"]


async def test_request_errors_and_cancelled_calls_do_not_count_against_health(tmp_path):
    clock = Clock()
    too_large = error_from_response("a", 413, {}, "Request too large")
    router, calls = make(tmp_path, [("a", {}), ("b", {})], {"a": [too_large], "b": [OK]}, clock)
    for _ in range(4):
        await ask(router)
        clock.now += 400
    health = next(r for r in router.status() if r["name"] == "a")["health"]
    assert health["calls"] == 0 and health["demoted_for"] == 0


async def test_latency_of_successes_is_reported(tmp_path):
    router, _ = make(tmp_path, [("a", {})], {"a": [OK]})
    await ask(router)
    health = router.status()[0]["health"]
    assert health["ok"] == 1 and health["success_rate"] == 1.0 and health["p50_ms"] is not None


async def test_health_ordering_can_be_switched_off(tmp_path):
    clock = Clock()
    router, calls = make(tmp_path, [("a", {}), ("b", {})], {"a": [fail()], "b": [OK]}, clock, health_order=False)
    await demote_a(router, clock)
    calls.clear()
    await ask(router)
    assert calls == ["a", "b"]


async def test_a_deep_model_picked_by_voice_still_answers_fast_turns(tmp_path):
    router, calls = make(tmp_path, [("a", {}), ("d", {"tier": "deep"})], {"a": [OK], "d": [OK]})
    data = router._store.config.model_dump(mode="json")
    data["preferred_provider"] = "d"
    router._store.update(data)
    await ask(router)
    assert calls == ["d"]


async def test_one_failure_does_not_reorder(tmp_path):
    clock = Clock()
    router, calls = make(tmp_path, [("a", {}), ("b", {})], {"a": [fail(), OK], "b": [OK]}, clock)
    await ask(router)
    clock.now += 400
    calls.clear()
    await ask(router)
    assert calls == ["a"]


@pytest.mark.parametrize("preferred, deep_fallback, health", [
    ("", False, True), ("leaky", False, True), ("leaky", True, True), ("leaky", True, False), ("", True, False),
])
async def test_private_turns_never_reach_a_provider_that_may_not_see_them(tmp_path, preferred, deep_fallback, health):
    clock = Clock()
    router, calls = make(tmp_path, [("leaky", {"allow_sensitive": False}), ("deepleak", {"tier": "deep",
                                    "allow_sensitive": False}), ("safe", {})],
                         {"leaky": [OK], "deepleak": [OK], "safe": [fail(), OK]}, clock,
                         allow_deep_fallback=deep_fallback, health_order=health)
    data = router._store.config.model_dump(mode="json")
    data["preferred_provider"] = preferred
    router._store.update(data)
    for _ in range(3):
        try:
            [e async for e in router.stream([Message("user", "my diary")], [], sensitive=True)]
        except LLMError:
            pass
        clock.now += 400
    assert set(calls) <= {"safe"}
