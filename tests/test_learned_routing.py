"""Learned routing table (W0-25): which provider succeeds fastest for each kind of request, from your own use."""
import asyncio

from tests.test_router import Clock
from minty_core.config import ConfigStore
from minty_core.keystore import SecretsStore
from minty_core.llm.router import LEARN_MIN, ProviderRouter
from minty_core.llm.types import LLMError, Message, TextDelta, ToolSpec


class Paced:
    def __init__(self, name, delay, fail=False):
        self.name, self.delay, self.fail = name, delay, fail

    async def stream(self, messages, tools):
        await asyncio.sleep(self.delay)
        if self.fail:
            raise LLMError("boom", status=500)
        yield TextDelta("ok")


def make(tmp_path, providers: dict, clock=None, **router_cfg):
    store = ConfigStore(tmp_path)
    data = store.load().model_dump(mode="json")
    data["providers"] = [{"name": n, "model": "m"} for n in providers]
    data["router"].update(router_cfg)
    store.update(data)
    secrets = SecretsStore(tmp_path)
    for n in providers:
        secrets.set(n, "key-" + n)
    calls = []

    def factory(cfg, key):
        calls.append(cfg.name)
        return providers[cfg.name]

    router = ProviderRouter(store, secrets, factory, clock or Clock(), state_path=tmp_path / "router-state.json")
    return router, calls


SMALL = [Message("user", "what time is it")]
LARGE = [Message("user", "plan my revision week for the maths exam")]


def teach(router, cls, name, ok, ms, times=LEARN_MIN):
    for _ in range(times):
        router._learn(cls, name, ok, ms)


async def test_the_fastest_reliable_provider_for_a_kind_of_request_goes_first(tmp_path):
    router, calls = make(tmp_path, {"a": Paced("a", 0), "b": Paced("b", 0)})
    teach(router, "small", "a", True, 2400.0)
    teach(router, "small", "b", True, 600.0)
    [_ async for _ in router.stream(SMALL, [])]
    [_ async for _ in router.stream(LARGE, [])]
    assert calls == ["b", "a"]  # learned for small requests only; large ones keep the configured order


async def test_a_provider_that_keeps_failing_a_kind_of_request_sinks(tmp_path):
    router, calls = make(tmp_path, {"a": Paced("a", 0), "b": Paced("b", 0)})
    teach(router, "large", "a", False, None)
    teach(router, "large", "b", True, 3000.0)
    [_ async for _ in router.stream(LARGE, [])]
    assert calls == ["b"]


async def test_requests_with_tools_are_learned_separately(tmp_path):
    router, calls = make(tmp_path, {"a": Paced("a", 0), "b": Paced("b", 0)})
    teach(router, "small+tools", "b", True, 500.0)
    teach(router, "small+tools", "a", True, 3000.0)
    [_ async for _ in router.stream(SMALL, [ToolSpec("clock_get_time", "time", {})])]
    [_ async for _ in router.stream(SMALL, [])]
    assert calls == ["b", "a"]


async def test_real_calls_teach_the_table(tmp_path):
    router, calls = make(tmp_path, {"slow": Paced("slow", 0.06), "fast": Paced("fast", 0.0)})
    for _ in range(LEARN_MIN):
        [_ async for _ in router.stream(SMALL, [], prefer="slow")]
        [_ async for _ in router.stream(SMALL, [], prefer="fast")]
    calls.clear()
    [_ async for _ in router.stream(SMALL, [])]
    assert calls == ["fast"]
    row = router.learned()["small"]
    assert row["fast"]["n"] == LEARN_MIN + 1 and row["slow"]["ms"] > row["fast"]["ms"]


async def test_the_table_survives_a_restart(tmp_path):
    router, _ = make(tmp_path, {"a": Paced("a", 0), "b": Paced("b", 0)})
    teach(router, "small", "a", True, 2400.0)
    teach(router, "small", "b", True, 600.0)
    router._save_state()
    again, calls = make(tmp_path, {"a": Paced("a", 0), "b": Paced("b", 0)})
    [_ async for _ in again.stream(SMALL, [])]
    assert calls == ["b"]


async def test_switched_off_keeps_the_configured_order(tmp_path):
    router, calls = make(tmp_path, {"a": Paced("a", 0), "b": Paced("b", 0)}, learned=False)
    teach(router, "small", "a", True, 2400.0)
    teach(router, "small", "b", True, 600.0)
    [_ async for _ in router.stream(SMALL, [])]
    assert calls == ["a"]


async def test_too_few_samples_change_nothing(tmp_path):
    router, calls = make(tmp_path, {"a": Paced("a", 0), "b": Paced("b", 0)})
    teach(router, "small", "b", True, 100.0, times=LEARN_MIN - 1)
    teach(router, "small", "a", True, 5000.0, times=LEARN_MIN - 1)
    [_ async for _ in router.stream(SMALL, [])]
    assert calls == ["a"]
