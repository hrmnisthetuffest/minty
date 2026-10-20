import asyncio

import pytest

from tests.plugin_helpers import make_host


class FakeSubAgent:
    def __init__(self, sentences=("Found three options.", "The best is X."), delay=0.0, error=None):
        self.sentences, self.delay, self.error, self.prompts = sentences, delay, error, []
        self.force_private = False

    async def respond(self, text, *, sensitive=False):
        self.prompts.append(text)
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        for s in self.sentences:
            yield s


def setup(tmp_path, agent=None, services=None, **settings):
    heard, made = [], []

    def factory(tier="fast"):
        made.append(tier)
        return agent or FakeSubAgent()

    host = make_host(tmp_path, notify=heard.append, services={"agent_factory": factory, **(services or {})},
                     plugins={"subagents": settings} if settings else None)
    return host, heard, made


async def wait_for(pred, timeout=2.0):
    for _ in range(int(timeout / 0.01)):
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out")


async def test_spawn_runs_in_background_and_reports_by_voice(tmp_path):
    agent = FakeSubAgent()
    host, heard, made = setup(tmp_path, agent)
    assert "Started subagent 1" in await host.call("subagents_spawn_subagent", {"task": "compare laptops"})
    await wait_for(lambda: heard)
    assert heard == ["Subagent 1 finished. Found three options. The best is X."]
    assert made == ["fast"] and "compare laptops" in agent.prompts[0]
    assert "done" in await host.call("subagents_subagent_status", {"id": 1})


async def test_deep_flag_selects_deep_tier(tmp_path):
    host, _, made = setup(tmp_path)
    await host.call("subagents_spawn_subagent", {"task": "prove it", "deep": True})
    assert made == ["deep"]


async def test_concurrency_cap_stop_and_status(tmp_path):
    host, heard, _ = setup(tmp_path, FakeSubAgent(delay=5), max_concurrent=1)
    await host.call("subagents_spawn_subagent", {"task": "slow"})
    assert (await host.call("subagents_spawn_subagent", {"task": "another"})).startswith("Error: RuntimeError")
    await asyncio.sleep(0.01)  # the job starts on the next tick
    assert "running" in await host.call("subagents_subagent_status", {})
    assert await host.call("subagents_stop_subagent", {"id": 1}) == "Stopped subagent 1."
    assert "No running subagent" in await host.call("subagents_stop_subagent", {"id": 1})
    await asyncio.sleep(0.05)
    assert heard == []


async def test_stop_cancels_and_awaits_the_task(tmp_path):
    host, _, _ = setup(tmp_path, FakeSubAgent(delay=5))
    await host.call("subagents_spawn_subagent", {"task": "slow"})
    runner = host._state["subagents"]["runner"]
    await host.call("subagents_stop_subagent", {"id": 1})
    assert runner.get(1).status == "cancelled" and not runner._tasks
    assert "cancelled" in await host.call("subagents_subagent_status", {"id": 1})


async def test_timeout_and_failure_are_announced(tmp_path):
    host, heard, _ = setup(tmp_path, FakeSubAgent(delay=5), max_runtime_s=0.1)
    await host.call("subagents_spawn_subagent", {"task": "slow"})
    await wait_for(lambda: heard)
    assert heard == ["Subagent 1 ran out of time."]
    host, heard, _ = setup(tmp_path, FakeSubAgent(error=RuntimeError("boom")))
    await host.call("subagents_spawn_subagent", {"task": "x"})
    await wait_for(lambda: heard)
    assert heard[0].startswith("Subagent 1 failed")


async def test_unavailable_and_empty_task(tmp_path):
    host = make_host(tmp_path)
    assert await host.call("subagents_spawn_subagent", {"task": "x"}) == "Subagents are unavailable."
    host, _, _ = setup(tmp_path)
    assert (await host.call("subagents_spawn_subagent", {"task": "  "})).startswith("Error: ValueError")


async def test_notification_result_is_capped_at_400_chars(tmp_path):
    host, heard, _ = setup(tmp_path, FakeSubAgent(sentences=("x" * 900,)))
    await host.call("subagents_spawn_subagent", {"task": "long"})
    await wait_for(lambda: heard)
    assert heard == ["Subagent 1 finished. " + "x" * 400]


async def test_private_conversation_makes_the_subagent_private(tmp_path):
    agent = FakeSubAgent()
    host, _, _ = setup(tmp_path, agent, services={"is_private": lambda: True})
    await host.call("subagents_spawn_subagent", {"task": "x"})
    assert agent.force_private is True


async def test_public_conversation_leaves_the_subagent_public(tmp_path):
    agent = FakeSubAgent()
    host, _, _ = setup(tmp_path, agent, services={"is_private": lambda: False})
    await host.call("subagents_spawn_subagent", {"task": "x"})
    assert agent.force_private is False


async def test_subagent_follows_the_main_conversation_going_private_mid_run(tmp_path):
    from tests.test_agent import make_agent
    from minty_core.llm.types import TextDelta, ToolCall, ToolCallEvent
    from minty_core.plugins.host import Tool

    private = [False]
    tool = Tool("t_work", "t", {"type": "object", "properties": {}}, lambda a: "done")
    rounds = [[ToolCallEvent(ToolCall("c1", "t_work", {}))], [TextDelta("All set.")]]
    agent, router, fake_host = make_agent(tmp_path, rounds, [tool])

    async def call(name, args):
        private[0] = True  # the user turns private mode on while the subagent is working
        return "done"

    fake_host.call = call
    host, heard, _ = setup(tmp_path, agent, services={"is_private": lambda: private[0]})
    await host.call("subagents_spawn_subagent", {"task": "x"})
    await wait_for(lambda: heard)
    assert router.sensitive == [False, True]


@pytest.mark.parametrize("settings", [
    {"max_concurrent": "lots", "max_runtime_s": "soon"},
    {"max_concurrent": -3, "max_runtime_s": 0},
    {"max_concurrent": True, "max_runtime_s": [1]},
    {"max_concurrent": {"a": 1}, "max_runtime_s": ""},
])
async def test_bad_settings_fall_back_to_defaults_and_never_fail_the_load(tmp_path, settings):
    host, _, _ = setup(tmp_path, FakeSubAgent(delay=5), **settings)
    assert host.get_tool("subagents_spawn_subagent") is not None
    for i in range(3):
        assert "Started subagent" in await host.call("subagents_spawn_subagent", {"task": f"t{i}"})
    assert (await host.call("subagents_spawn_subagent", {"task": "t4"})).startswith("Error: RuntimeError")
    await host._state["subagents"]["runner"].cancel_all()


async def test_numeric_string_settings_are_accepted(tmp_path):
    host, _, _ = setup(tmp_path, FakeSubAgent(delay=5), max_concurrent="1")
    await host.call("subagents_spawn_subagent", {"task": "a"})
    assert (await host.call("subagents_spawn_subagent", {"task": "b"})).startswith("Error: RuntimeError")
    await host._state["subagents"]["runner"].cancel_all()


def test_non_finite_numbers_are_not_accepted():
    from minty_core.plugins.builtin.subagents.plugin import _positive

    assert _positive(float("nan"), 3, int) == 3 and _positive(float("inf"), 600.0, float) == 600.0
    assert _positive("2.9", 3, int) == 2 and _positive(0.5, 3, int) == 3 and _positive("1e2", 1.0, float) == 100.0


async def test_uses_the_shared_job_runner_when_present(tmp_path):
    from minty_core.jobs import JobRunner
    shared = JobRunner(None)
    host, heard, _ = setup(tmp_path, FakeSubAgent(delay=5), services={"jobs": shared})
    await host.call("subagents_spawn_subagent", {"task": "compare laptops"})
    [job] = shared.running()
    assert job.kind == "subagent" and job.goal == "compare laptops" and "subagents" not in host._state or \
        "runner" not in host._state.get("subagents", {})
    await shared.cancel_all()
