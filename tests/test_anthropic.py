import json

import httpx
import pytest

from minty_core.config import ProviderConfig
from minty_core.llm.anthropic import AnthropicProvider
from minty_core.llm.types import LLMError, Message, TextDelta, ToolCall, ToolCallEvent, ToolSpec


def make(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AnthropicProvider(ProviderConfig(name="anthropic", kind="anthropic", model="claude-sonnet-5-5"), "KEY", client)


async def collect(p, messages, tools=()):
    return [e async for e in p.stream(messages, list(tools))]


async def test_text_and_tool_use_blocks_become_events_and_request_shape():
    seen = {}

    def handler(req):
        seen["headers"] = req.headers
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"content": [
            {"type": "text", "text": "Checking."},
            {"type": "tool_use", "id": "t1", "name": "clock_get_time", "input": {}},
        ]})

    tools = [ToolSpec("clock_get_time", "time", {"type": "object", "properties": {}})]
    events = await collect(make(handler), [Message("system", "Be brief."), Message("user", "time?")], tools)
    assert events == [TextDelta("Checking."), ToolCallEvent(ToolCall("t1", "clock_get_time", {}))]
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "KEY" and seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["body"]["system"] == "Be brief."
    assert seen["body"]["tools"][0]["input_schema"] == {"type": "object", "properties": {}}
    assert seen["body"]["messages"] == [{"role": "user", "content": [{"type": "text", "text": "time?"}]}]


async def test_consecutive_tool_results_merge_into_one_user_message():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    messages = [
        Message("user", "go"),
        Message("assistant", "", tool_calls=[ToolCall("a", "x_one", {}), ToolCall("b", "x_two", {})]),
        Message("tool", "r1", tool_call_id="a", name="x_one"),
        Message("tool", "r2", tool_call_id="b", name="x_two"),
    ]
    await collect(make(handler), messages)
    msgs = seen["body"]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert [b["tool_use_id"] for b in msgs[2]["content"]] == ["a", "b"]


async def test_http_error_becomes_llm_error():
    def handler(req):
        return httpx.Response(529, text="overloaded")

    with pytest.raises(LLMError) as err:
        await collect(make(handler), [Message("user", "hi")])
    assert err.value.status == 529


async def test_non_json_200_body_raises_llm_error():
    def handler(req):
        return httpx.Response(200, text="<html>Bad Response</html>")

    with pytest.raises(LLMError) as err:
        await collect(make(handler), [Message("user", "hi")])
    assert "malformed response" in str(err.value)


async def test_null_content_raises_llm_error():
    def handler(req):
        return httpx.Response(200, json={"content": None})

    with pytest.raises(LLMError) as err:
        await collect(make(handler), [Message("user", "hi")])
    assert "malformed response" in str(err.value)


async def test_tool_use_missing_id_raises_llm_error():
    def handler(req):
        return httpx.Response(200, json={"content": [
            {"type": "tool_use", "name": "clock_get_time", "input": {}},
        ]})

    with pytest.raises(LLMError) as err:
        await collect(make(handler), [Message("user", "hi")])
    assert "malformed response" in str(err.value)


async def test_in_body_error_raises_llm_error():
    def handler(req):
        return httpx.Response(200, json={"error": {"message": "overloaded"}})

    with pytest.raises(LLMError) as err:
        await collect(make(handler), [Message("user", "hi")])
    assert "overloaded" in str(err.value)


async def test_network_error_becomes_llm_error():
    def handler(req):
        raise httpx.ConnectError("down")

    with pytest.raises(LLMError) as err:
        await collect(make(handler), [Message("user", "hi")])
    assert "down" in str(err.value)


async def test_extra_is_ignored_by_the_anthropic_adapter():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    cfg = ProviderConfig(name="anthropic", kind="anthropic", model="claude-sonnet-5-5",
                         extra={"reasoning_effort": "max", "model": "evil"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AnthropicProvider(cfg, "KEY", client)
    assert await collect(provider, [Message("user", "hi")]) == [TextDelta("ok")]
    assert "reasoning_effort" not in seen["body"] and seen["body"]["model"] == "claude-sonnet-5-5"


async def test_request_uses_the_providers_own_timeout():
    seen = []

    def handler(req):
        seen.append(req.extensions["timeout"])
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = ProviderConfig(name="anthropic", kind="anthropic", model="m", timeout_s=75.0)
    await collect(AnthropicProvider(cfg, "KEY", client), [Message("user", "hi")])
    await collect(make(handler), [Message("user", "hi")])
    assert seen[0]["read"] == 75.0 and seen[0]["connect"] == 10.0
    assert seen[1]["read"] == 30.0
