"""Provider errors classified by cause (W0-03): request-shaped errors never cool a healthy provider."""
import json

import httpx
import pytest

from tests.test_router import Clock, P, collect, make_router
from minty_core.config import ProviderConfig
from minty_core.httpclient import error_from_response
from minty_core.llm.openai_compat import OpenAICompatProvider
from minty_core.llm.types import LLMError, Message, RequestTooLarge, TextDelta, ToolNotOffered

GROQ_413 = json.dumps({"error": {
    "message": "Request too large for model `openai/gpt-oss-120b` in organization `org_x` service tier `on_demand` on "
               "tokens per minute (TPM): Limit 8000, Requested 9512, please reduce your message size and try again.",
    "type": "tokens", "code": "rate_limit_exceeded"}})
GROQ_TOOL = json.dumps({"error": {
    "message": "tool call validation failed: attempted to call tool 'browser_click' which was not in request.tools",
    "type": "invalid_request_error", "code": "tool_use_failed"}})


@pytest.mark.parametrize("status, body, kind", [
    (429, "rate limited", "rate_limit"),
    (413, GROQ_413, "too_large"),
    (400, '{"error":{"message":"This model\'s maximum context length is 8192 tokens"}}', "too_large"),
    (400, '{"error":{"code":"context_length_exceeded"}}', "too_large"),
    (400, GROQ_TOOL, "tool_not_offered"),
    (401, "invalid api key", "auth"),
    (403, "forbidden", "auth"),
    (500, "oops", "server"),
    (502, "bad gateway", "server"),
    (503, "unavailable", "server"),
    (529, "overloaded", "server"),
    (408, "timeout", "timeout"),
    (504, "gateway timeout", "timeout"),
    (404, "model not found", "other"),
    (400, "bad request", "other"),
])
def test_http_errors_are_classified(status, body, kind):
    err = error_from_response("groq", status, {}, body)
    assert isinstance(err, LLMError) and err.kind == kind and err.status == status


def test_request_shaped_errors_are_their_own_types():
    too_large = error_from_response("groq", 413, {}, GROQ_413)
    tool = error_from_response("groq", 400, {}, GROQ_TOOL)
    assert isinstance(too_large, RequestTooLarge)
    assert isinstance(tool, ToolNotOffered) and tool.tool == "browser_click"


def test_retry_after_is_kept_and_plain_errors_still_work():
    assert error_from_response("groq", 429, {"retry-after": "7"}, "slow down").retry_after == 7.0
    assert LLMError("no LLM provider available").kind == "other"
    assert LLMError("x", kind="timeout").kind == "timeout"


def provider(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatProvider(ProviderConfig(name="groq", base_url="https://api.test/v1", model="m"), "K", client)


async def stream(p):
    return [e async for e in p.stream([Message("user", "hi")], [])]


async def test_timeouts_and_unreachable_hosts_are_classified():
    def slow(req):
        raise httpx.ReadTimeout("timed out", request=req)

    def down(req):
        raise httpx.ConnectError("refused", request=req)

    with pytest.raises(LLMError) as e:
        await stream(provider(slow))
    assert e.value.kind == "timeout"
    with pytest.raises(LLMError) as e:
        await stream(provider(down))
    assert e.value.kind == "server"


async def test_error_inside_the_stream_is_classified():
    body = ("data: " + GROQ_TOOL + "\n\n").encode()
    with pytest.raises(ToolNotOffered) as e:
        await stream(provider(lambda req: httpx.Response(200, content=body)))
    assert e.value.tool == "browser_click"


@pytest.mark.parametrize("err", [
    error_from_response("a", 413, {}, GROQ_413),
    error_from_response("a", 400, {}, GROQ_TOOL),
])
async def test_request_shaped_errors_never_cool_the_provider(tmp_path, err):
    router, calls = make_router(tmp_path, [P("a"), P("b")], ["a", "b"], {"a": [err], "b": [TextDelta("ok")]})
    assert await collect(router) == [TextDelta("ok")]
    calls.clear()
    await collect(router)
    assert calls[0] == "a"  # still first in line next time
    row = next(r for r in router.status() if r["name"] == "a")
    assert row["cooling_for"] == 0.0 and row["last_error_kind"] == err.kind


async def test_rate_limit_still_cools_and_honours_retry_after(tmp_path):
    clock = Clock()
    err = error_from_response("a", 429, {"retry-after": "120"}, "slow down")
    router, calls = make_router(tmp_path, [P("a"), P("b")], ["a", "b"], {"a": [err], "b": [TextDelta("ok")]}, clock)
    await collect(router)
    assert next(r for r in router.status() if r["name"] == "a")["cooling_for"] == 120.0
    calls.clear()
    clock.now += 61
    await collect(router)
    assert calls == ["b"]


async def test_old_behaviour_when_classification_is_switched_off(tmp_path):
    err = error_from_response("a", 413, {}, GROQ_413)
    router, calls = make_router(tmp_path, [P("a"), P("b")], ["a", "b"], {"a": [err], "b": [TextDelta("ok")]})
    data = router._store.config.model_dump(mode="json")
    data["router"]["classify_errors"] = False
    router._store.update(data)
    await collect(router)
    calls.clear()
    await collect(router)
    assert calls == ["b"]


async def test_llm_call_trace_records_the_error_kind(tmp_path):
    from minty_core.trace import Tracer, read_events
    err = error_from_response("a", 413, {}, GROQ_413)
    router, _ = make_router(tmp_path, [P("a"), P("b")], ["a", "b"], {"a": [err], "b": [TextDelta("ok")]})
    router.tracer = Tracer(tmp_path / "t")
    await collect(router)
    router.tracer.close()
    kinds = [e.get("error_kind") for e in read_events(tmp_path / "t") if e["kind"] == "llm_call"]
    assert kinds == ["too_large", None]
