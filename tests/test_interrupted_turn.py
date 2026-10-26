"""An answer that is cut off after something already changed must not erase the fact that it changed."""
import asyncio

import pytest

from minty_core.llm.types import LLMError, TextDelta, ToolCall, ToolCallEvent
from minty_core.plugins.host import Tool
from tests.test_agent import make_agent, speak

OBJ = {"type": "object", "properties": {}}


def send_tool(**kw):
    return Tool("mail_send", "send a message", OBJ, lambda a: "sent", risky=True, **kw)


def read_tool():
    return Tool("mail_read", "read messages", OBJ, lambda a: "three messages")


async def test_a_finished_change_survives_an_interrupted_answer(tmp_path):
    rounds = [[ToolCallEvent(ToolCall("c1", "mail_send", {"to": "sam"}))], LLMError("provider down"),
              [TextDelta("Did that go out?")], [TextDelta("Yes, it was sent earlier.")]]
    agent, router, host = make_agent(tmp_path, rounds, [send_tool()])
    with pytest.raises(LLMError):
        await speak(agent, "text sam")
    assert [m.role for m in agent.history] == ["user", "assistant"]
    assert agent.history[0].content == "text sam"
    assert "cut off" in agent.history[1].content and "mail send" in agent.history[1].content
    await speak(agent, "did it go?")
    seen = " ".join(m.content for m in router.calls[-1])
    assert "mail send" in seen and "cut off" in seen  # the next turn knows the message went out


async def test_reads_and_failed_changes_still_roll_back_completely(tmp_path):
    agent, _, host = make_agent(tmp_path, [[ToolCallEvent(ToolCall("c1", "mail_read", {}))], LLMError("down")],
                                [read_tool()])
    with pytest.raises(LLMError):
        await speak(agent)
    assert agent.history == []
    agent, _, host = make_agent(tmp_path, [[ToolCallEvent(ToolCall("c1", "mail_send", {}))], LLMError("down")],
                                [send_tool()])

    async def failing(name, args):
        return "Error: the server said no"

    host.call = failing
    with pytest.raises(LLMError):
        await speak(agent)
    assert agent.history == []  # nothing changed, so there is nothing to remember


async def test_a_declined_change_is_not_remembered_as_done(tmp_path):
    agent, _, _ = make_agent(tmp_path, [[ToolCallEvent(ToolCall("c1", "mail_send", {}))], LLMError("down")],
                             [send_tool()], confirm_answer=False)
    with pytest.raises(LLMError):
        await speak(agent)
    assert agent.history == []


async def test_cancelling_after_a_change_keeps_the_note_too(tmp_path):
    agent, router, host = make_agent(tmp_path, [[ToolCallEvent(ToolCall("c1", "mail_send", {}))], [TextDelta("Sent.")]],
                                     [send_tool()])
    original = router.stream

    async def slow_second(messages, tools, *, sensitive=False, tier="fast"):
        if len(router.calls) >= 1:
            await asyncio.sleep(5)
        async for ev in original(messages, tools, sensitive=sensitive, tier=tier):
            yield ev

    router.stream = slow_second
    task = asyncio.create_task(speak(agent))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [m.role for m in agent.history] == ["user", "assistant"] and "mail send" in agent.history[1].content


async def test_the_next_turn_does_not_inherit_the_last_turns_changes(tmp_path):
    rounds = [[ToolCallEvent(ToolCall("c1", "mail_send", {}))], [TextDelta("Sent.")], LLMError("down")]
    agent, _, _ = make_agent(tmp_path, rounds, [send_tool()])
    assert await speak(agent, "send it") == ["Sent."]
    before = [(m.role, m.content) for m in agent.history]
    with pytest.raises(LLMError):
        await speak(agent, "and now?")
    assert [(m.role, m.content) for m in agent.history] == before  # the first turn's send is not blamed on the second
    assert not any("cut off" in m.content for m in agent.history)
