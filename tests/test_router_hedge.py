"""Hedged second call (W0-07): a slow first provider gets company; the first token wins, the other is cancelled."""
import asyncio
import time

import pytest

from tests.test_router import P
from minty_core.config import ConfigStore
from minty_core.keystore import SecretsStore
from minty_core.llm.router import ProviderRouter
from minty_core.llm.types import DeadlineExceeded, LLMError, Message, TextDelta
from minty_core.trace import Tracer, read_events


class Timed:
    """Yields [(delay_before, text), ...]; an Exception item is raised after its delay."""

    def __init__(self, name, steps):
        self.name, self.steps = name, steps
        self.cancelled = False

    async def stream(self, messages, tools):
        try:
            for delay, item in self.steps:
                await asyncio.sleep(delay)
                if isinstance(item, Exception):
                    raise item
                yield TextDelta(item)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        except GeneratorExit:
            self.cancelled = True
            raise


def build(tmp_path, providers: dict, hedge=True, hedge_after=0.05, deadline=0.0):
    store = ConfigStore(tmp_path)
    data = store.load().model_dump(mode="json")
    data["providers"] = [P(n) for n in providers]
    data["router"]["hedge"] = hedge
    data["router"]["hedge_after_s"] = hedge_after
    data["turn"]["first_token_deadline_s"] = deadline
    store.update(data)
    secrets = SecretsStore(tmp_path)
    for n in providers:
        secrets.set(n, "key-" + n)
    started = []

    def factory(cfg, key):
        started.append(cfg.name)
        return providers[cfg.name]

    router = ProviderRouter(store, secrets, factory)
    router.tracer = Tracer(tmp_path / "traces")
    return router, started


async def ask(router):
    return [e.text async for e in router.stream([Message("user", "hi")], [])]


async def test_slow_first_provider_is_overtaken_and_cancelled(tmp_path):
    a, b = Timed("a", [(0.5, "A")]), Timed("b", [(0.0, "B1"), (0.02, "B2")])
    router, started = build(tmp_path, {"a": a, "b": b})
    t0 = time.monotonic()
    assert await ask(router) == ["B1", "B2"]
    assert time.monotonic() - t0 < 0.4 and started == ["a", "b"] and a.cancelled
    router.tracer.close()
    calls = {e["provider"]: e for e in read_events(tmp_path / "traces") if e["kind"] == "llm_call"}
    assert calls["a"]["outcome"] == "cancelled" and calls["b"]["outcome"] == "ok"
    assert calls["b"]["hedged"] is True and not calls["a"].get("hedged")


async def test_quick_first_provider_needs_no_hedge(tmp_path):
    a, b = Timed("a", [(0.0, "A")]), Timed("b", [(0.0, "B")])
    router, started = build(tmp_path, {"a": a, "b": b}, hedge_after=0.2)
    assert await ask(router) == ["A"] and started == ["a"]


async def test_the_original_can_still_win(tmp_path):
    a, b = Timed("a", [(0.15, "A")]), Timed("b", [(0.6, "B")])
    router, started = build(tmp_path, {"a": a, "b": b})
    assert await ask(router) == ["A"] and started == ["a", "b"] and b.cancelled


async def test_an_early_error_fails_over_as_usual(tmp_path):
    a, b = Timed("a", [(0.0, LLMError("x", status=429))]), Timed("b", [(0.0, "B")])
    router, started = build(tmp_path, {"a": a, "b": b}, hedge_after=0.3)
    assert await ask(router) == ["B"] and started == ["a", "b"]


async def test_error_from_one_racer_leaves_the_other_running(tmp_path):
    a, b = Timed("a", [(0.3, "A")]), Timed("b", [(0.0, LLMError("y", status=500))])
    router, started = build(tmp_path, {"a": a, "b": b, "c": Timed("c", [(0.0, "C")])})
    out = await ask(router)
    assert out in (["A"], ["C"]) and started[:2] == ["a", "b"]


async def test_hedging_respects_the_deadline(tmp_path):
    a, b = Timed("a", [(5.0, "A")]), Timed("b", [(5.0, "B")])
    router, _ = build(tmp_path, {"a": a, "b": b}, deadline=0.3)
    t0 = time.monotonic()
    with pytest.raises(DeadlineExceeded):
        await ask(router)
    assert time.monotonic() - t0 < 1.0 and a.cancelled and b.cancelled


async def test_off_by_default_means_one_provider_at_a_time(tmp_path):
    store = ConfigStore(tmp_path / "d")
    assert store.load().router.hedge is False
    a, b = Timed("a", [(0.2, "A")]), Timed("b", [(0.0, "B")])
    router, started = build(tmp_path, {"a": a, "b": b}, hedge=False)
    assert await ask(router) == ["A"] and started == ["a"]


async def test_consumer_stopping_early_cancels_both_racers(tmp_path):
    a, b = Timed("a", [(0.5, "A")]), Timed("b", [(0.0, "B1"), (5.0, "B2")])
    router, _ = build(tmp_path, {"a": a, "b": b})
    stream = router.stream([Message("user", "hi")], [])
    first = await anext(stream)
    await stream.aclose()
    assert first.text == "B1" and a.cancelled and b.cancelled
