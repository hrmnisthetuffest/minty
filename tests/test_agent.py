import asyncio

import pytest

from minty_core.agent import Agent
from minty_core.config import ConfigStore
from minty_core.llm.types import LLMError, TextDelta, ToolCall, ToolCallEvent, ToolSpec
from minty_core.memory import MemoryStore
from minty_core.personality import PersonalityStore
from minty_core.plugins.host import Tool


class ScriptedRouter:
    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.calls = []
        self.closed = 0
        self.sensitive = []
        self.tiers = []

    async def stream(self, messages, tools, *, sensitive=False, tier="fast"):
        self.calls.append(list(messages))
        self.sensitive.append(sensitive)
        self.tiers.append(tier)
        item = self.rounds.pop(0)
        if isinstance(item, Exception):
            raise item
        try:
            for ev in item:
                yield ev
        finally:
            self.closed += 1


class FakeHost:
    def __init__(self, tools=(), raise_for=()):
        self.tools = {t.name: t for t in tools}
        self.called = []
        self.raise_for = set(raise_for)

    def tool_specs(self):
        return [ToolSpec(t.name, t.description, t.parameters) for t in self.tools.values()]

    def get_tool(self, name):
        return self.tools.get(name)

    async def call(self, name, args):
        self.called.append((name, args))
        if name in self.raise_for:
            raise RuntimeError(f"Tool {name} failed")
        return "tool-result"


def make_agent(tmp_path, rounds, tools=(), confirm_answer=True, raise_for=(), **kw):
    store = ConfigStore(tmp_path)
    store.load()
    personalities = PersonalityStore(tmp_path)
    personalities.ensure_defaults()

    async def confirm(prompt):
        return confirm_answer

    router, host = ScriptedRouter(rounds), FakeHost(tools, raise_for=raise_for)
    agent = Agent(router, host, store, personalities, MemoryStore(tmp_path / "memory.md"), confirm, **kw)
    return agent, router, host


async def speak(agent, text="hi"):
    return [s async for s in agent.respond(text)]


async def test_plain_reply_yields_sentences_and_records_history(tmp_path):
    agent, router, _ = make_agent(tmp_path, [[TextDelta("It is "), TextDelta("noon. Anything else?")]])
    assert await speak(agent) == ["It is noon.", "Anything else?"]
    assert [m.role for m in agent.history] == ["user", "assistant"]
    assert router.calls[0][0].role == "system" and "spoken aloud" in router.calls[0][0].content


async def test_tool_round_trip(tmp_path):
    call = ToolCall("c1", "clock_get_time", {})
    tool = Tool("clock_get_time", "time", {"type": "object", "properties": {}}, lambda a: "x")
    agent, router, host = make_agent(tmp_path, [[ToolCallEvent(call)], [TextDelta("It is noon.")]], [tool])
    assert await speak(agent) == ["It is noon."]
    assert host.called == [("clock_get_time", {})]
    second_call = router.calls[1]
    assert second_call[-1].role == "tool" and second_call[-1].content == "tool-result"
    assert second_call[-2].tool_calls == [call]


@pytest.mark.parametrize("answer, expect_called", [(True, 1), (False, 0)])
async def test_risky_tool_needs_confirmation(tmp_path, answer, expect_called):
    call = ToolCall("c1", "lock_unlock", {})
    tool = Tool("lock_unlock", "unlock", {"type": "object", "properties": {}}, lambda a: "x", risky=True)
    agent, _, host = make_agent(tmp_path, [[ToolCallEvent(call)], [TextDelta("Done.")]], [tool], confirm_answer=answer)
    await speak(agent)
    assert len(host.called) == expect_called
    if not answer:
        assert agent.history[-2].content == "The user declined."


async def test_llm_failure_rolls_back_history_and_next_turn_works(tmp_path):  # RF4
    agent, _, _ = make_agent(tmp_path, [LLMError("down"), [TextDelta("Back.")]])
    with pytest.raises(LLMError):
        await speak(agent)
    assert agent.history == []
    assert await speak(agent) == ["Back."]
    assert len(agent.history) == 2


async def test_round_limit_yields_fallback(tmp_path):
    call = ToolCall("c1", "t_t", {})
    tool = Tool("t_t", "t", {"type": "object", "properties": {}}, lambda a: "x")
    agent, _, _ = make_agent(tmp_path, [[ToolCallEvent(call)], [ToolCallEvent(call)]], [tool], max_rounds=2)
    assert await speak(agent) == ["I couldn't finish that request."]


def _loop_tool():
    return Tool("t_t", "t", {"type": "object", "properties": {}}, lambda a: "x")


async def test_repeating_the_same_call_is_warned_then_stopped(tmp_path):
    rounds = [[ToolCallEvent(ToolCall(f"c{i}", "t_t", {"q": "same"}))] for i in range(5)]
    agent, router, host = make_agent(tmp_path, rounds, [_loop_tool()], max_rounds=8)
    out = await speak(agent)
    assert len(host.called) == 4  # the 4th identical call is the second trip and ends the turn
    assert len(router.calls) == 4  # no 5th model call was made
    assert "going in circles" in out[-1] and "I stopped" in out[-1]
    warned = [m for m in agent.history if m.role == "tool" and "Do not repeat" in m.content]
    assert len(warned) == 1 and warned[0].tool_call_id == "c2"  # the first trip is a tool result the model can act on
    assert agent.history[-1].role == "assistant" and "going in circles" in agent.history[-1].content


async def test_a_run_of_failures_trips_the_guard_even_with_different_arguments(tmp_path):
    rounds = [[ToolCallEvent(ToolCall(f"c{i}", "t_t", {"n": i}))] for i in range(6)]
    agent, router, host = make_agent(tmp_path, rounds, [_loop_tool()], max_rounds=8)

    async def failing(name, args):
        host.called.append((name, args))
        return f"Error: attempt {args['n']} failed"

    host.call = failing
    out = await speak(agent)
    assert len(host.called) == 5 and "going in circles" in out[-1]  # 4th failure warns, 5th ends it


async def test_different_calls_with_different_results_are_never_a_loop(tmp_path):
    rounds = [[ToolCallEvent(ToolCall(f"c{i}", "t_t", {"n": i}))] for i in range(4)] + [[TextDelta("All done.")]]
    agent, router, host = make_agent(tmp_path, rounds, [_loop_tool()], max_rounds=8)

    async def varied(name, args):
        host.called.append((name, args))
        return f"result {args['n']}"

    host.call = varied
    assert await speak(agent) == ["All done."]
    assert not [m for m in agent.history if "Do not repeat" in (m.content or "")]


async def test_history_trim_starts_on_user_message(tmp_path):
    agent, _, _ = make_agent(tmp_path, [[TextDelta(f"r{i}.")] for i in range(5)], max_history=4)
    for i in range(5):
        await speak(agent, f"q{i}")
    assert len(agent.history) <= 4 and agent.history[0].role == "user"


async def test_missing_personality_falls_back(tmp_path):
    agent, router, _ = make_agent(tmp_path, [[TextDelta("ok.")]])
    for name in agent._personalities.list():
        agent._personalities.delete(name)
    assert await speak(agent) == ["ok."]
    assert "Minty" in router.calls[0][0].content


async def test_close_mid_text(tmp_path):
    """Close the generator mid-text and verify history rollback and stream closure."""
    agent, router, _ = make_agent(tmp_path, [[TextDelta("One. Two. Three.")]])
    gen = agent.respond("hi")
    first = await gen.__anext__()
    assert first == "One."
    await gen.aclose()
    assert agent.history == []
    assert router.closed == 1


async def test_cancel_during_confirm(tmp_path):
    """Cancel during confirm and verify history rollback."""
    event = asyncio.Event()

    async def blocking_confirm(prompt):
        await event.wait()
        return True

    call = ToolCall("c1", "lock_unlock", {})
    tool = Tool("lock_unlock", "unlock", {"type": "object", "properties": {}}, lambda a: "x", risky=True)
    store = ConfigStore(tmp_path)
    store.load()
    personalities = PersonalityStore(tmp_path)
    personalities.ensure_defaults()

    router, host = ScriptedRouter([[ToolCallEvent(call)], [TextDelta("Done.")]]), FakeHost([tool])
    agent = Agent(router, host, store, personalities, MemoryStore(tmp_path / "memory.md"), blocking_confirm)

    async def consume():
        async for _ in agent.respond("hi"):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert agent.history == []


async def test_tool_exception_rolls_back(tmp_path):
    """Tool exception rolls back history."""
    call = ToolCall("c1", "failing_tool", {})
    tool = Tool("failing_tool", "fail", {"type": "object", "properties": {}}, lambda a: "x")
    agent, _, _ = make_agent(tmp_path, [[ToolCallEvent(call)], [TextDelta("Done.")]], [tool], raise_for=["failing_tool"])
    with pytest.raises(RuntimeError):
        await speak(agent)
    assert agent.history == []


async def test_concurrent_respond_calls_are_serialized(tmp_path):
    agent, router, _ = make_agent(tmp_path, [[TextDelta("First.")], [TextDelta("Second.")]])
    real_stream = router.stream

    async def slow_first(messages, tools, *, sensitive=False, tier="fast"):
        if not router.calls:
            await asyncio.sleep(0.1)
        async for ev in real_stream(messages, tools, sensitive=sensitive, tier=tier):
            yield ev

    router.stream = slow_first
    await asyncio.gather(speak(agent, "one"), speak(agent, "two"))
    assert [m.role for m in agent.history] == ["user", "assistant", "user", "assistant"]
    assert [m.content for m in agent.history] == ["one", "First.", "two", "Second."]


async def test_sensitive_tool_result_is_routed_privately(tmp_path):
    call = ToolCall("c1", "journal_read_entries", {})
    tool = Tool("journal_read_entries", "read", {"type": "object", "properties": {}}, lambda a: "x", sensitive=True)
    agent, router, _ = make_agent(tmp_path, [[ToolCallEvent(call)], [TextDelta("You wrote about tea.")]], [tool])
    await speak(agent)
    assert router.sensitive == [False, True]


def _journal_tool():
    return Tool("journal_read_entries", "read", {"type": "object", "properties": {}}, lambda a: "x", sensitive=True)


async def test_private_history_keeps_next_turn_private(tmp_path):
    call = ToolCall("c1", "journal_read_entries", {})
    agent, router, _ = make_agent(
        tmp_path, [[ToolCallEvent(call)], [TextDelta("You wrote about tea.")], [TextDelta("Sure.")]], [_journal_tool()]
    )
    await speak(agent)
    await speak(agent, "thanks")
    assert router.sensitive == [False, True, True]


async def test_routing_returns_to_normal_after_private_messages_trimmed(tmp_path):
    call = ToolCall("c1", "journal_read_entries", {})
    rounds = [[ToolCallEvent(call)], [TextDelta("Tea.")], [TextDelta("A.")], [TextDelta("B.")]]
    agent, router, _ = make_agent(tmp_path, rounds, [_journal_tool()], max_history=4)
    for text in ("one", "two", "three"):
        await speak(agent, text)
    assert router.sensitive == [False, True, True, False]


async def test_tool_message_after_sensitive_tool_is_private(tmp_path):
    call = ToolCall("c1", "journal_read_entries", {})
    agent, _, _ = make_agent(tmp_path, [[ToolCallEvent(call)], [TextDelta("Tea.")]], [_journal_tool()])
    await speak(agent)
    assert [m.private for m in agent.history] == [False, False, True, True]


async def test_agent_passes_its_tier(tmp_path):
    agent, router, _ = make_agent(tmp_path, [[TextDelta("ok.")]], tier="deep")
    await speak(agent)
    assert router.tiers == ["deep"]


async def test_tier_does_not_change_private_routing(tmp_path):
    call = ToolCall("c1", "journal_read_entries", {})
    agent, router, _ = make_agent(
        tmp_path, [[ToolCallEvent(call)], [TextDelta("You wrote about tea.")]], [_journal_tool()], tier="deep"
    )
    await speak(agent)
    assert router.sensitive == [False, True] and router.tiers == ["deep", "deep"]


async def test_force_private_routes_every_round_sensitively(tmp_path):
    call = ToolCall("c1", "clock_get_time", {})
    tool = Tool("clock_get_time", "t", {"type": "object", "properties": {}}, lambda a: "x")
    agent, router, _ = make_agent(tmp_path, [[ToolCallEvent(call)], [TextDelta("Noon.")]], [tool])
    agent.force_private = True
    await speak(agent)
    assert router.sensitive == [True, True]


async def test_force_private_defaults_to_false(tmp_path):
    agent, router, _ = make_agent(tmp_path, [[TextDelta("Hi.")]])
    assert agent.force_private is False
    await speak(agent)
    assert router.sensitive == [False]


async def test_tier_is_public_and_a_change_applies_on_the_next_round(tmp_path):
    call = ToolCall("c1", "clock_get_time", {})
    tool = Tool("clock_get_time", "t", {"type": "object", "properties": {}}, lambda a: "x")
    agent, router, _ = make_agent(tmp_path, [[ToolCallEvent(call)], [TextDelta("Noon.")], [TextDelta("Again.")]], [tool])
    assert agent.tier == "fast"
    original = agent._run_call

    async def switching(c):
        agent.tier = "deep"  # as a spoken "thinking mode" command would, mid-turn
        return await original(c)

    agent._run_call = switching
    await speak(agent)
    assert router.tiers == ["fast", "deep"]
    agent.tier = "fast"
    await speak(agent)
    assert router.tiers == ["fast", "deep", "fast"]


class RecordingConfirm:
    def __init__(self):
        self.prompts = []

    async def __call__(self, prompt):
        self.prompts.append(prompt)
        return True


async def _confirm_prompt(tmp_path, tool, args):
    agent, _, _ = make_agent(tmp_path, [[ToolCallEvent(ToolCall("c1", tool.name, args))], [TextDelta("Done.")]], [tool])
    agent.confirm = RecordingConfirm()
    await speak(agent)
    return agent.confirm.prompts


def _risky(name="lock_unlock", describe=None):
    return Tool(name, "d", {"type": "object", "properties": {}}, lambda a: "x", risky=True, describe=describe)


async def test_generic_confirmation_names_the_tool_and_args(tmp_path):
    prompts = await _confirm_prompt(tmp_path, _risky(), {"entity": "lock.front", "force": True})
    assert prompts == ["Should I run lock unlock with entity lock.front, force True?"]


async def test_generic_confirmation_without_args_is_short(tmp_path):
    assert await _confirm_prompt(tmp_path, _risky(), {}) == ["Should I run lock unlock?"]


async def test_generic_confirmation_masks_secrets_and_caps_length(tmp_path):
    args = {"api_key": "sk-supersecret", "Auth_Token": "t0ken", "password": "hunter2", "note": "x" * 400}
    (prompt,) = await _confirm_prompt(tmp_path, _risky(), args)
    assert "sk-supersecret" not in prompt and "t0ken" not in prompt and "hunter2" not in prompt
    assert "api_key …" in prompt and len(prompt) <= 160


async def test_describe_is_used_when_provided(tmp_path):
    tool = _risky(describe=lambda a: f"Should I unlock {a['door']}?")
    assert await _confirm_prompt(tmp_path, tool, {"door": "the front door"}) == ["Should I unlock the front door?"]


@pytest.mark.parametrize("bad", [lambda a: 1 / 0, lambda a: "", lambda a: None])
async def test_failing_or_empty_describe_falls_back_to_generic(tmp_path, bad):
    prompts = await _confirm_prompt(tmp_path, _risky(describe=bad), {"door": "front"})
    assert prompts == ["Should I run lock unlock with door front?"]


async def test_private_probe_is_consulted_live_each_round(tmp_path):
    flag = [False]
    call = ToolCall("c1", "clock_get_time", {})

    tool = Tool("clock_get_time", "t", {"type": "object", "properties": {}}, lambda a: "x")
    agent, router, host = make_agent(tmp_path, [[ToolCallEvent(call)], [TextDelta("Noon.")]], [tool],
                                     private_probe=lambda: flag[0])

    async def flip(name, args):  # the conversation turns private while the tool runs
        flag[0] = True
        return "x"

    host.call = flip
    await speak(agent)
    assert router.sensitive == [False, True]


async def test_private_probe_failure_is_logged_and_treated_as_public(tmp_path, caplog):
    def boom():
        raise RuntimeError("probe down")

    agent, router, _ = make_agent(tmp_path, [[TextDelta("Hi.")]], private_probe=boom)
    with caplog.at_level("WARNING"):
        await speak(agent)
    assert router.sensitive == [False]
    assert "private_probe" in caplog.text


async def test_private_probe_defaults_to_none_and_force_private_still_applies(tmp_path):
    agent, router, _ = make_agent(tmp_path, [[TextDelta("Hi.")]], private_probe=lambda: False)
    agent.force_private = True
    await speak(agent)
    assert router.sensitive == [True]


async def test_old_messages_age_out_of_context_but_stay_in_the_archive(tmp_path):
    import time

    from minty_core.history import ConversationArchive

    archive = ConversationArchive(tmp_path / "history")
    agent, _, _ = make_agent(tmp_path, [[TextDelta("Paris.")], [TextDelta("Berlin.")]], archive=archive)
    await speak(agent, "capital of France?")
    assert len(agent.history) == 2
    for m in agent.history:
        m.ts = time.time() - 31 * 60  # half an hour ago, plus a minute
    await speak(agent, "capital of Germany?")
    assert [m.content for m in agent.history] == ["capital of Germany?", "Berlin."]
    day = archive.raw_days()[-1]
    assert "capital of France?" in archive.raw_day(day) and "Berlin." in archive.raw_day(day)


async def test_prompt_extras_reach_the_system_prompt_and_failures_are_skipped(tmp_path):
    agent, _, _ = make_agent(tmp_path, [[TextDelta("ok")]])
    agent.prompt_extras.append(lambda: "\n## History\nyesterday we talked about jazz")
    agent.prompt_extras.append(lambda: 1 / 0)
    prompt = agent._system_prompt()
    assert "yesterday we talked about jazz" in prompt


async def test_confirm_when_asks_only_for_the_calls_it_flags(tmp_path):

    calls = []

    def run(args):
        calls.append(args["ref"])
        return "ok"

    tool = Tool("browser_click", "c", {"type": "object", "properties": {}}, run,
                confirm_when=lambda a: a["ref"] == "buy")
    asked = []
    agent, _, host = make_agent(
        tmp_path, [[ToolCallEvent(ToolCall("1", "browser_click", {"ref": "buy"}))], [TextDelta("no")],
                   ], tools=[tool], confirm_answer=False)
    async def confirm(prompt):
        asked.append(prompt)
        return False
    agent.confirm = confirm
    await speak(agent, "buy it")
    assert asked and calls == []


async def test_reset_conversation_clears_context_and_keeps_the_archive(tmp_path):
    from minty_core.history import ConversationArchive

    archive = ConversationArchive(tmp_path / "history")
    agent, _, _ = make_agent(tmp_path, [[TextDelta("Paris.")], [TextDelta("Rome.")]], archive=archive)
    agent.prompt_extras.append(lambda: archive.context_block(30))  # what build_context wires up
    await speak(agent, "capital of France?")
    assert "capital of France?" not in agent._system_prompt()  # still live context, not history
    agent.force_private = True
    assert agent.reset_conversation() == 2
    assert agent.history == [] and agent.force_private is True  # a mode setting, not conversation
    assert "capital of France?" in agent._system_prompt()  # now shown as earlier today
    await speak(agent, "capital of Italy?")
    assert [m.content for m in agent.history] == ["capital of Italy?", "Rome."]
