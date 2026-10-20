"""Typing to Minty in the terminal: the same answer path as the local API, shown as a conversation on the display."""
import pytest

from minty_core.context import build_context
from minty_core.llm.types import LLMError
from minty_core.typed import answer_text, typed_chat


async def fake_respond(reply):
    async def respond(text, **kw):
        for part in reply:
            yield part

    return respond


async def test_a_typed_question_goes_through_the_commands_then_the_assistant(tmp_path):
    ctx = build_context(tmp_path)
    heard = []

    async def respond(text, **kw):
        heard.append(text)
        yield "It is noon."
        yield "Anything else?"

    ctx.agent.respond = respond
    assert await answer_text(ctx, "what time is it") == "It is noon. Anything else?" and heard == ["what time is it"]


async def test_a_command_phrase_is_answered_by_the_command_not_the_assistant(tmp_path):
    ctx = build_context(tmp_path)

    async def respond(text, **kw):
        raise AssertionError("a command must not reach the assistant")
        yield ""

    ctx.agent.respond = respond
    reply = await answer_text(ctx, "switch to auto")
    assert reply  # the router answered


async def test_an_assistant_failure_is_raised_for_the_caller_to_word(tmp_path):
    ctx = build_context(tmp_path)

    async def respond(text, **kw):
        raise LLMError("all providers are down")
        yield ""

    ctx.agent.respond = respond
    with pytest.raises(LLMError):
        await answer_text(ctx, "hello")


async def test_typed_chat_shows_you_then_minty_on_the_display(tmp_path):
    ctx = build_context(tmp_path)
    events = ctx.bus.subscribe({"transcript", "notice", "error"})

    async def respond(text, **kw):
        yield "Hello there."

    ctx.agent.respond = respond
    await typed_chat(ctx, "hi")
    seen = [(e["type"], e.get("role"), e.get("text") or e.get("message")) for e in list(events._queue)]
    assert seen == [("transcript", "user", "hi"), ("transcript", "assistant", "Hello there.")]


async def test_typed_chat_says_so_when_the_voice_is_busy_or_the_models_are_down(tmp_path):
    ctx = build_context(tmp_path)
    events = ctx.bus.subscribe({"transcript", "notice", "error"})
    ctx.pipeline = type("P", (), {"state": "speaking"})()
    await typed_chat(ctx, "hi")
    assert [e["type"] for e in list(events._queue)] == ["notice"]  # not now: it is talking
    ctx.pipeline = type("P", (), {"state": "idle"})()
    events._queue.clear()

    async def respond(text, **kw):
        raise LLMError("down")
        yield ""

    ctx.agent.respond = respond
    await typed_chat(ctx, "hi")
    kinds = [(e["type"], e.get("role")) for e in list(events._queue)]
    assert kinds[0] == ("transcript", "user") and kinds[-1][0] == "error"
