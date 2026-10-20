"""Failure injection exercises the real router paths (W1-23)."""
import pytest

from tests.test_router import P
from tests.test_turn_deadline import Slow, router_with
from minty_core.failures import KINDS, FaultInjector
from minty_core.llm.types import LLMError, Message, TextDelta


async def run(router, tier="fast"):
    return [e async for e in router.stream([Message("user", "hi")], [], tier=tier)]


def inject(router, **debug):
    data = router._store.config.model_dump(mode="json")
    data["debug"].update(debug)
    router._store.update(data)


def test_the_picks_follow_the_rates_and_the_seed():
    inj = FaultInjector()
    inj.configure(5)
    first = [inj.pick({"rate_limit": 0.3, "timeout": 0.2}, "groq", []) for _ in range(2000)]
    inj.configure(6)
    inj.configure(5)  # same seed again: the same run
    again = [inj.pick({"rate_limit": 0.3, "timeout": 0.2}, "groq", []) for _ in range(2000)]
    assert first == again
    assert 0.25 < first.count("rate_limit") / 2000 < 0.35 and 0.15 < first.count("timeout") / 2000 < 0.25
    assert inj.pick({"rate_limit": 1.0}, "groq", ["gemini"]) is None  # only named providers
    assert inj.pick({"nonsense": 1.0}, "groq", []) is None and inj.pick({}, "groq", []) is None


@pytest.mark.parametrize("kind,status", [("rate_limit", 429), ("too_large", 413), ("server", 503), ("auth", 401)])
def test_an_injected_error_is_classified_like_the_real_one(kind, status):
    err = FaultInjector.error(kind, "groq")
    assert isinstance(err, LLMError) and err.kind == kind and err.status == status and err.provider == "groq"
    assert FaultInjector.error("timeout", "groq").kind == "timeout"
    assert set(KINDS) == {"rate_limit", "too_large", "server", "auth", "timeout"}


async def test_the_router_fails_over_from_an_injected_provider(tmp_path):
    a, b = Slow("a", 0.0, text="from a"), Slow("b", 0.0, text="from b")
    router, called = router_with(tmp_path, {"a": a, "b": b}, deadline=2.0)
    inject(router, inject={"rate_limit": 1.0}, only=["a"], seed=1)
    events = await run(router)
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["from b"]
    assert called[0] == "a"  # a was tried, failed with the injected 429, and b answered


async def test_when_every_provider_is_injected_the_error_keeps_its_kind(tmp_path):
    router, _ = router_with(tmp_path, {"a": Slow("a", 0.0), "b": Slow("b", 0.0)}, deadline=2.0)
    inject(router, inject={"server": 1.0}, seed=2)
    with pytest.raises(LLMError):
        await run(router)
    assert {row["name"]: row["last_error_kind"] for row in router.status()} == {"a": "server", "b": "server"}


async def test_nothing_is_injected_by_default(tmp_path):
    router, _ = router_with(tmp_path, {"a": Slow("a", 0.0, text="ok")}, deadline=2.0)
    assert [e.text for e in await run(router) if isinstance(e, TextDelta)] == ["ok"]
