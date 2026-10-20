"""First-token deadline, honest failure and the one-time "one moment" notice (W0-05)."""
import asyncio
import time

import pytest

from tests.fakes import FakeAgent
from tests.test_pipeline import build, finish, wait_until
from tests.test_router import P
from minty_core.config import ConfigStore
from minty_core.keystore import SecretsStore
from minty_core.llm.router import ProviderRouter
from minty_core.llm.types import DeadlineExceeded, LLMError, Message, TextDelta
from minty_core.pipeline import BRAIN_DOWN, MODELS_SLOW, ONE_MOMENT


class Slow:
    def __init__(self, name, delay, text="ok", fail=None):
        self.name, self.delay, self.text, self.fail = name, delay, text, fail
        self.cancelled = False

    async def stream(self, messages, tools):
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.fail is not None:
            raise self.fail
        yield TextDelta(self.text)


def router_with(tmp_path, providers: dict, deadline=0.3, tiers=None):
    store = ConfigStore(tmp_path)
    data = store.load().model_dump(mode="json")
    data["providers"] = [P(n, tier=(tiers or {}).get(n, "fast")) for n in providers]
    data["turn"]["first_token_deadline_s"] = deadline
    store.update(data)
    secrets = SecretsStore(tmp_path)
    for n in providers:
        secrets.set(n, "key-" + n)
    called = []

    def factory(cfg, key):
        called.append(cfg.name)
        return providers[cfg.name]

    return ProviderRouter(store, secrets, factory), called


async def run(router, tier="fast"):
    return [e async for e in router.stream([Message("user", "hi")], [], tier=tier)]


async def test_hung_provider_hits_the_deadline_quickly(tmp_path):
    a = Slow("a", 5.0)
    router, called = router_with(tmp_path, {"a": a, "b": Slow("b", 0.0)}, deadline=0.3)
    started = time.monotonic()
    with pytest.raises(DeadlineExceeded) as e:
        await run(router)
    assert time.monotonic() - started < 1.0
    assert e.value.kind == "timeout" and a.cancelled and called == ["a"]


async def test_failures_inside_the_deadline_still_fail_over(tmp_path):
    router, called = router_with(tmp_path, {"a": Slow("a", 0.0, fail=LLMError("x", status=429)),
                                            "b": Slow("b", 0.05, "from b")}, deadline=0.5)
    assert await run(router) == [TextDelta("from b")] and called == ["a", "b"]


async def test_next_provider_gets_only_the_time_that_is_left(tmp_path):
    router, called = router_with(tmp_path, {"a": Slow("a", 0.25, fail=LLMError("x", status=500)),
                                            "b": Slow("b", 1.0)}, deadline=0.4)
    started = time.monotonic()
    with pytest.raises(DeadlineExceeded):
        await run(router)
    assert time.monotonic() - started < 0.8 and called == ["a", "b"]


async def test_deep_tier_and_zero_deadline_wait_as_before(tmp_path):
    router, _ = router_with(tmp_path / "deep", {"a": Slow("a", 0.3, "deep answer")}, deadline=0.1, tiers={"a": "deep"})
    assert await run(router, tier="deep") == [TextDelta("deep answer")]
    router, _ = router_with(tmp_path / "off", {"a": Slow("a", 0.3, "slow answer")}, deadline=0)
    assert await run(router) == [TextDelta("slow answer")]


def set_turn(p, **turn):
    data = p.store.config.model_dump(mode="json")
    data["turn"].update(turn)
    p.store.update(data)


class DelayedAgent(FakeAgent):
    """Sentences come with pauses: [(delay_before, sentence), ...] per respond() call."""

    async def respond(self, text, *, sensitive=False):
        self.heard.append(text)
        for delay, sentence in self.replies.pop(0):
            await asyncio.sleep(delay)
            yield sentence


async def test_slow_answer_gets_one_moment_once(tmp_path):
    p, f = build(tmp_path, stt_texts=["open chrome"])
    p.agent = f.agent = DelayedAgent([[(0.3, "Chrome is open."), (0.3, "Anything else?")]])
    set_turn(p, notice_after_s=0.1)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: b"Anything else?" in f.player.played)
    await finish(p, f, task)
    assert f.player.played == [ONE_MOMENT.encode(), b"Chrome is open.", b"Anything else?"]


async def test_quick_answer_gets_no_notice(tmp_path):
    p, f = build(tmp_path, stt_texts=["time"])
    p.agent = f.agent = DelayedAgent([[(0.0, "Noon.")]])
    set_turn(p, notice_after_s=0.2)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: b"Noon." in f.player.played)
    await asyncio.sleep(0.3)
    await finish(p, f, task)
    assert f.player.played == [b"Noon."]


async def test_notice_switched_off(tmp_path):
    p, f = build(tmp_path, stt_texts=["x"])
    p.agent = f.agent = DelayedAgent([[(0.2, "Late.")]])
    set_turn(p, notice_after_s=0)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: b"Late." in f.player.played)
    await finish(p, f, task)
    assert f.player.played == [b"Late."]


async def test_deadline_is_spoken_honestly_and_the_next_turn_works(tmp_path):
    p, f = build(tmp_path, stt_texts=["one", "two"], replies=[DeadlineExceeded("no model answered"), ["Fine now."]])
    set_turn(p, notice_after_s=0)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: MODELS_SLOW.encode() in f.player.played)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: b"Fine now." in f.player.played)
    await finish(p, f, task)
    assert BRAIN_DOWN.encode() not in f.player.played
