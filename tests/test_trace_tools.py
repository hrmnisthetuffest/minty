"""Tool calls and confirmations end up in the trace."""
import pytest

from tests.test_agent import make_agent
from tests.test_plugin_host import make_host, write_plugin
from minty_core.llm.types import TextDelta, ToolCall, ToolCallEvent
from minty_core.plugins.host import Tool


class RecordingTracer:
    def __init__(self):
        self.events = []

    def event(self, kind, **data):
        self.events.append({"kind": kind, **data})


async def test_tool_calls_are_traced_ok_error_and_unknown(tmp_path):
    write_plugin(tmp_path / "plugins")
    host, _ = make_host(tmp_path)
    tracer = RecordingTracer()
    host._services["tracer"] = tracer
    host.reload()
    assert await host.call("demo_hello", {"who": "me", "api_key": "sk-123456789"}) == "hi me"
    assert (await host.call("demo_boom", {})).startswith("Error: RuntimeError")
    assert (await host.call("demo_nope", {})).startswith("Error: unknown tool")
    ok, boom, unknown = tracer.events
    assert ok["name"] == "demo_hello" and ok["ok"] is True and ok["result_chars"] == 5 and ok["ms"] >= 0
    assert ok["args"]["who"] == "me"
    assert boom["ok"] is False and "kaput" in boom["error"]
    assert unknown["ok"] is False and unknown["error"] == "unknown tool"


@pytest.mark.parametrize("answer", [True, False])
async def test_confirmations_are_traced_with_the_answer(tmp_path, answer):
    tool = Tool("risky_thing", "d", {"type": "object", "properties": {}}, lambda a: "did it", risky=True)
    call = ToolCall("1", "risky_thing", {})
    agent, router, host = make_agent(tmp_path, [[ToolCallEvent(call)], [TextDelta("Done.")]], [tool],
                                     confirm_answer=answer)
    agent.tracer = RecordingTracer()
    [s async for s in agent.respond("do it")]
    [event] = agent.tracer.events
    assert event["kind"] == "confirm" and event["tool"] == "risky_thing" and event["answer"] is answer
    assert event["prompt"].startswith("Should I")
