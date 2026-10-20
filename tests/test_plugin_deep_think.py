import asyncio
import importlib.util
import logging

import pytest

from tests.plugin_helpers import make_host
from minty_core.config import ConfigStore
from minty_core.context import build_context
from minty_core.keystore import SecretsStore
from minty_core.llm.router import ProviderRouter
from minty_core.llm.types import TextDelta
from minty_core.plugins.host import BUILTIN_PLUGIN_DIR


def _plugin_module():
    # Plugins are loaded by path (not importable by name), so load the module the same way the host does.
    spec = importlib.util.spec_from_file_location("deep_think_under_test", BUILTIN_PLUGIN_DIR / "deep_think" / "plugin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeRouter:
    def __init__(self, chunks, delay=0.0):
        self.chunks, self.delay, self.calls = chunks, delay, []

    async def stream(self, messages, tools, *, sensitive=False, tier="fast"):
        self.calls.append({"messages": messages, "tools": tools, "tier": tier, "sensitive": sensitive})
        await asyncio.sleep(self.delay)
        for c in self.chunks:
            yield TextDelta(c)


async def test_uses_deep_tier_without_tools_and_strips_thinking(tmp_path):
    router = FakeRouter(["<think>let me see... 17*3", "=51</think>", "The answer is 51."])
    host = make_host(tmp_path, services={"router": router})
    out = await host.call("deep_think_think_deeply", {"question": "What is 17 times 3?", "context": "school maths"})
    assert out == "The answer is 51."
    call = router.calls[0]
    assert call["tier"] == "deep" and call["tools"] == []
    assert "school maths" in call["messages"][-1].content and "17 times 3" in call["messages"][-1].content


async def test_routes_sensitively_when_the_conversation_is_private(tmp_path):
    router = FakeRouter(["ok"])
    host = make_host(tmp_path, services={"router": router, "is_private": lambda: True})
    await host.call("deep_think_think_deeply", {"question": "q"})
    assert router.calls[0]["sensitive"] is True
    router = FakeRouter(["ok"])
    host = make_host(tmp_path, services={"router": router, "is_private": lambda: False})
    await host.call("deep_think_think_deeply", {"question": "q"})
    assert router.calls[0]["sensitive"] is False
    router = FakeRouter(["ok"])
    await make_host(tmp_path, services={"router": router}).call("deep_think_think_deeply", {"question": "q"})
    assert router.calls[0]["sensitive"] is False


async def test_unfinished_thinking_gives_a_clear_message(tmp_path):
    host = make_host(tmp_path, services={"router": FakeRouter(["<think>still going"])})
    assert await host.call("deep_think_think_deeply", {"question": "q"}) == "I couldn't reach a conclusion."


async def test_timeout(tmp_path):
    host = make_host(tmp_path, services={"router": FakeRouter(["x"], delay=1.0)},
                     plugins={"deep_think": {"timeout_s": 0.1}})
    assert "too long" in await host.call("deep_think_think_deeply", {"question": "q"})


async def test_without_router_and_empty_question(tmp_path):
    host = make_host(tmp_path)
    assert await host.call("deep_think_think_deeply", {"question": "q"}) == "Deep thinking is unavailable."
    host = make_host(tmp_path, services={"router": FakeRouter(["x"])})
    assert (await host.call("deep_think_think_deeply", {"question": " "})).startswith("Error: ValueError")


def test_build_context_shares_the_router_with_plugins(tmp_path):
    ctx = build_context(tmp_path)
    assert ctx.services["router"] is ctx.router
    assert "deep_think_think_deeply" in [t.name for t in ctx.host.tool_specs()]


@pytest.mark.parametrize("raw, expected", [
    ("<think>a</think>b", "b"),
    ("<think>a</think>one <think>b</think>two", "one two"),
    ("<THINK>a</Think>Answer", "Answer"),
    ("<thinking>plan</thinking> Answer", "Answer"),
    ("<think>a<think>b</think>c</think>Answer", "Answer"),
    ("reasoning out loud</think>Answer", "Answer"),
    ("x</think>y</THINKING>Answer", "Answer"),
    ("Answer<think>never finished", "Answer"),
    ("<think>a</think>ok<think>unfinished", "ok"),
    ("Just a plain answer, 3 < 5 and 7 > 2.", "Just a plain answer, 3 < 5 and 7 > 2."),
    ("<think>only thoughts</think>", ""),
    ("", ""),
])
def test_strip_thinking(raw, expected):
    assert _plugin_module()._strip_thinking(raw) == expected


async def test_only_thinking_gives_the_no_conclusion_message(tmp_path):
    host = make_host(tmp_path, services={"router": FakeRouter(["<think>hmm</think>"])})
    assert await host.call("deep_think_think_deeply", {"question": "q"}) == "I couldn't reach a conclusion."


@pytest.mark.parametrize("bad", ["soon", 0, -5, 901, float("nan"), None])
def test_bad_timeout_setting_falls_back_to_120_with_a_warning(bad, caplog):
    module = _plugin_module()
    with caplog.at_level(logging.WARNING):
        assert module._timeout({"timeout_s": bad}) == 120.0
    assert bool(caplog.records) == (bad is not None)


def test_valid_timeout_setting_is_used():
    module = _plugin_module()
    assert module._timeout({"timeout_s": "45"}) == 45.0 and module._timeout({"timeout_s": 900}) == 900.0


class HangingProvider:
    def __init__(self):
        self.closed = False

    async def stream(self, messages, tools):
        try:
            await asyncio.sleep(30)
            yield TextDelta("never")
        finally:
            self.closed = True


async def test_timeout_closes_the_underlying_provider_stream(tmp_path):
    provider = HangingProvider()
    store = ConfigStore(tmp_path)
    store.load()
    data = store.config.model_dump(mode="json")
    data["providers"] = [{"name": "slow", "model": "m", "tier": "deep"}]
    store.update(data)
    secrets = SecretsStore(tmp_path)
    secrets.set("slow", "k")
    router = ProviderRouter(store, secrets, lambda cfg, key: provider)
    host = make_host(tmp_path, services={"router": router}, plugins={"deep_think": {"timeout_s": 0.1}})
    assert "too long" in await host.call("deep_think_think_deeply", {"question": "q"})
    assert provider.closed is True


def test_plugins_and_app_context_share_one_services_dict(tmp_path):
    ctx = build_context(tmp_path)
    assert ctx.host._services is ctx.services


async def test_think_in_background_returns_at_once_and_follows_up():
    import asyncio
    from minty_core.jobs import JobRunner
    from tests.plugin_helpers import make_host
    import tempfile
    from pathlib import Path

    heard = []

    class SlowRouter:
        sensitive = []

        async def stream(self, messages, tools, *, sensitive=False, tier="fast", prefer=None):
            from minty_core.llm.types import TextDelta
            SlowRouter.sensitive.append(sensitive)
            await asyncio.sleep(0.2)
            yield TextDelta("<think>hmm</think>Yes, it holds. The numbers add up.")

    runner = JobRunner(None, notify=heard.append)
    with tempfile.TemporaryDirectory() as tmp:
        host = make_host(Path(tmp), services={"router": SlowRouter(), "jobs": runner, "is_private": lambda: True})
        out = await host.call("deep_think_think_in_background", {"question": "Does my budget work?"})
        assert out.startswith("Started.") and runner.running()[0].kind == "think"
        job = await runner.wait(1)
    assert job.status == "done" and job.private is True and SlowRouter.sensitive == [True]
    assert heard == ["Update on Does my budget work?: Yes, it holds. The numbers add up."]


async def test_quick_answers_only_suppresses_background_thinking():
    from minty_core.jobs import JobRunner
    from tests.plugin_helpers import make_host
    import tempfile
    from pathlib import Path
    runner = JobRunner(None)
    with tempfile.TemporaryDirectory() as tmp:
        host = make_host(Path(tmp), services={"router": object(), "jobs": runner, "quick_only": lambda: True})
        out = await host.call("deep_think_think_in_background", {"question": "x"})
    assert "quick answers only" in out and runner.select() == []
