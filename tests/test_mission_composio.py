"""A background mission reads through the user's own Composio the way the conversation does: no question for a read, a question for anything that acts.

The safety gate asks about data going to a destination the user never mentioned. The conversation is told which destinations the user set
up themselves (an MCP server they added at the terminal); the helper agents built for missions and sub-agents were not, so a mission's
read-only `COMPOSIO_SEARCH_TOOLS` was asked about every time (mission 14, "Should I run COMPOSIO_SEARCH_TOOLS ...")."""
import pytest

from minty_core.context import build_context
from minty_core.llm.types import ToolCall
from minty_core.mcp.registry import McpRegistry, tool_hash

SEARCH = {"name": "COMPOSIO_SEARCH_TOOLS", "description": "d", "inputSchema": {"type": "object"}}
EXECUTE = {"name": "COMPOSIO_MULTI_EXECUTE_TOOL", "description": "d", "inputSchema": {"type": "object"}}
FETCH = {"tools": [{"tool_slug": "GMAIL_FETCH_EMAILS", "arguments": {"max_results": 5}, "account": "a"}], "sync_response_to_workbench": False}
SEND = {"tools": [{"tool_slug": "GMAIL_SEND_EMAIL", "arguments": {"recipient_email": "x@y.test", "subject": "s", "body": "b"}, "account": "a"}],
        "sync_response_to_workbench": False}


@pytest.fixture
def ctx(tmp_path):
    reg = McpRegistry(tmp_path / "mcp.json")
    reg.add_remote("composio", "https://connect.composio.dev/mcp", {"x-consumer-api-key": "COMPOSIO_API_KEY"})
    reg.record_tools("composio", [SEARCH, EXECUTE])
    reg.allow("composio", "COMPOSIO_SEARCH_TOOLS", tool_hash(SEARCH), readonly=True)
    reg.allow("composio", "COMPOSIO_MULTI_EXECUTE_TOOL", tool_hash(EXECUTE), readonly=False)
    c = build_context(tmp_path)
    data = c.store.config.model_dump(mode="json")
    data["mcp"]["enabled"] = True
    c.store.update(data)
    return c


def verdict(ctx, helper, tool_name, arguments):
    tool = ctx.services["get_tool"]("mcp_call")
    call = ToolCall("1", "mcp_call", {"server": "composio", "tool": tool_name, "arguments": arguments})
    known, prompt, extra = helper._gate_inputs(tool, call)
    return helper.guard.assess(tool, "mcp_call", call.arguments, prompt, extra_risk=extra, known=known, request="", user_origin=False)


def test_a_mission_searching_composio_for_a_tool_is_not_asked(ctx):
    helper = ctx.services["mission_worker"]("tidy up the promotions in my gmail", None).helper
    a = verdict(ctx, helper, "COMPOSIO_SEARCH_TOOLS", {"queries": [{"use_case": "list Gmail messages in promotions"}]})
    assert a.decision == "allow" and a.risk == 1


def test_a_mission_reading_mail_is_not_asked_but_sending_it_still_is(ctx):
    helper = ctx.services["mission_worker"]("tidy up the promotions in my gmail", None).helper
    assert verdict(ctx, helper, "COMPOSIO_MULTI_EXECUTE_TOOL", FETCH).decision == "allow"
    sent = verdict(ctx, helper, "COMPOSIO_MULTI_EXECUTE_TOOL", SEND)
    assert sent.decision == "ask" and sent.risk == 3


def test_a_sub_agent_gets_the_same_trusted_places_as_the_conversation(ctx):
    helper = ctx.services["agent_factory"]()
    assert verdict(ctx, helper, "COMPOSIO_SEARCH_TOOLS", {"queries": []}).decision == "allow"
