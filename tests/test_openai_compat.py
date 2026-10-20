import json

import httpx
import pytest

from minty_core.config import ProviderConfig
from minty_core.llm.openai_compat import OpenAICompatProvider
from minty_core.llm.types import LLMError, Message, TextDelta, ToolCall, ToolCallEvent, ToolSpec


def sse(*chunks):
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return body.encode()


def make(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cfg = ProviderConfig(name="groq", base_url="https://api.test/v1", model="m1")
    return OpenAICompatProvider(cfg, "KEY", client)


async def collect(provider, messages=None, tools=()):
    return [e async for e in provider.stream(messages or [Message("user", "hi")], list(tools))]


async def test_streams_text_and_sends_expected_request():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers["authorization"]
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=sse(
            {"choices": [{"delta": {"content": "Hel"}}]},
            {"choices": [{"delta": {"content": "lo."}}]},
        ))

    events = await collect(make(handler))
    assert events == [TextDelta("Hel"), TextDelta("lo.")]
    assert seen["auth"] == "Bearer KEY"
    assert seen["url"] == "https://api.test/v1/chat/completions"
    assert seen["body"]["stream"] is True and seen["body"]["model"] == "m1"
    assert "tools" not in seen["body"]


async def test_tool_call_assembled_from_chunks_and_serialised():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=sse(
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "clock_get_time", "arguments": ""}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{\"a\":"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "1}"}}]}}]},
        ))

    messages = [
        Message("user", "hi"),
        Message("assistant", "", tool_calls=[ToolCall("c0", "x_y", {"k": "v"})]),
        Message("tool", "result", tool_call_id="c0", name="x_y"),
    ]
    tools = [ToolSpec("clock_get_time", "time", {"type": "object", "properties": {}})]
    events = await collect(make(handler), messages, tools)
    assert events == [ToolCallEvent(ToolCall("c1", "clock_get_time", {"a": 1}))]
    sent = seen["body"]
    assert sent["tools"][0]["function"]["name"] == "clock_get_time"
    assert sent["messages"][1]["tool_calls"][0]["function"]["arguments"] == json.dumps({"k": "v"})
    assert sent["messages"][2] == {"role": "tool", "tool_call_id": "c0", "content": "result"}


async def test_rate_limit_error_carries_status_and_retry_after():
    def handler(req):
        return httpx.Response(429, headers={"retry-after": "7"}, text="slow down")

    with pytest.raises(LLMError) as err:
        await collect(make(handler))
    assert err.value.status == 429 and err.value.retry_after == 7.0


async def test_malformed_tool_arguments_raise_llm_error():  # RF3
    def handler(req):
        return httpx.Response(200, content=sse(
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "t", "arguments": "{bad"}}]}}]},
        ))

    with pytest.raises(LLMError):
        await collect(make(handler))


async def test_network_error_becomes_llm_error():
    def handler(req):
        raise httpx.ConnectError("boom")

    with pytest.raises(LLMError):
        await collect(make(handler))


async def test_malformed_sse_data_line_raises_llm_error():
    def handler(req):
        return httpx.Response(200, content=b"data: {not json\n\n")

    with pytest.raises(LLMError):
        await collect(make(handler))


async def test_in_stream_provider_error_raises_llm_error():
    def handler(req):
        return httpx.Response(200, content=sse(
            {"error": {"message": "quota exceeded", "code": 429}},
        ))

    with pytest.raises(LLMError) as err:
        await collect(make(handler))
    assert err.value.status == 429
    assert "quota exceeded" in str(err.value)


async def test_extra_fields_are_merged_but_reserved_keys_are_not_overridable():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=sse({"choices": [{"delta": {"content": "ok"}}]}))

    extra = {"reasoning_effort": "max", "model": "evil", "messages": [], "stream": False, "tools": [{"x": 1}],
             "tool_choice": "none", "functions": [{"x": 1}], "function_call": "none", "stream_options": {"a": 1}}
    cfg = ProviderConfig(name="nvidia-deep", base_url="https://api.test/v1", model="m1", extra=extra)
    provider = OpenAICompatProvider(cfg, "KEY", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    tool = ToolSpec("t", "d", {"type": "object", "properties": {}})
    assert await collect(provider, tools=[tool]) == [TextDelta("ok")]
    body = seen["body"]
    assert body["reasoning_effort"] == "max"
    assert body["model"] == "m1" and body["stream"] is True
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert [t["function"]["name"] for t in body["tools"]] == ["t"]
    for reserved in ("tool_choice", "functions", "function_call", "stream_options"):
        assert reserved not in body


async def test_no_extra_sends_only_the_base_payload():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=sse({"choices": [{"delta": {"content": "ok"}}]}))

    await collect(make(handler))
    assert set(seen["body"]) == {"model", "stream", "messages", "temperature"}
    assert seen["body"]["temperature"] == 0.6


async def test_request_uses_the_providers_own_timeout():
    seen = []

    def handler(req):
        seen.append(req.extensions["timeout"])
        return httpx.Response(200, content=sse({"choices": [{"delta": {"content": "ok"}}]}))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    for timeout_s in (None, 180.0):
        kw = {} if timeout_s is None else {"timeout_s": timeout_s}
        cfg = ProviderConfig(name="p", base_url="https://api.test/v1", model="m1", **kw)
        await collect(OpenAICompatProvider(cfg, "KEY", client))
    assert seen[0] == {"connect": 10.0, "read": 30.0, "write": 30.0, "pool": 30.0}
    assert seen[1] == {"connect": 10.0, "read": 180.0, "write": 180.0, "pool": 180.0}


async def test_gemini_thought_signature_is_kept_and_sent_back():
    seen = []
    sig = {"google": {"thought_signature": "SIG123"}}

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, content=sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1", "extra_content": sig, "function": {"name": "t", "arguments": "{}"}}]}}]}))

    cfg = ProviderConfig(name="gemini", base_url="https://generativelanguage.googleapis.com/v1beta/openai", model="g")
    provider = OpenAICompatProvider(cfg, "KEY", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    [event] = await collect(provider)
    assert event.call.extra == {"extra_content": sig}
    from_groq = ToolCall("c0", "t", {})  # an earlier call another provider made: no signature of its own
    history = [Message("user", "hi"), Message("assistant", "", tool_calls=[from_groq, event.call]),
               Message("tool", "r", tool_call_id="c1")]
    await collect(provider, history)
    replayed = seen[1]["messages"][1]["tool_calls"]
    assert replayed[0]["extra_content"] == {"google": {"thought_signature": "skip_thought_signature_validator"}}
    assert replayed[1]["extra_content"] == sig


async def test_other_providers_get_no_extra_content():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, content=sse({"choices": [{"delta": {"content": "ok"}}]}))

    history = [Message("user", "hi"), Message("assistant", "", tool_calls=[ToolCall("c0", "t", {})]),
               Message("tool", "r", tool_call_id="c0")]
    await collect(make(handler), history)
    assert "extra_content" not in seen["body"]["messages"][1]["tool_calls"][0]
