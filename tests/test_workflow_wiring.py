"""A mission is told what would make its result useful, and what is not available, before it starts."""
from minty_core.context import build_context
from minty_core.mcp.registry import McpRegistry, tool_hash

GOAL = "Find 20 promising companies in this market, investigate them, organize their information in a spreadsheet and rank the best ones"
EXECUTE = {"name": "COMPOSIO_MULTI_EXECUTE_TOOL", "description": "d", "inputSchema": {"type": "object"}}


def enable_mcp(ctx):
    data = ctx.store.config.model_dump(mode="json")
    data["mcp"]["enabled"] = True
    ctx.store.update(data)


def test_a_big_goal_gets_the_brief_and_says_honestly_that_no_sheet_tool_is_connected(tmp_path):
    ctx = build_context(tmp_path)
    text = ctx.services["workflow_brief"](GOAL)
    assert "No spreadsheet tool is connected" in text and "artifacts_list" not in text.split("No spreadsheet tool")[0]


def test_with_composio_ready_it_tells_the_worker_to_use_and_reuse_a_sheet(tmp_path):
    reg = McpRegistry(tmp_path / "mcp.json")
    reg.add_remote("composio", "https://connect.composio.dev/mcp", {"x-consumer-api-key": "COMPOSIO_API_KEY"})
    reg.record_tools("composio", [EXECUTE])
    reg.allow("composio", "COMPOSIO_MULTI_EXECUTE_TOOL", tool_hash(EXECUTE), readonly=False)
    ctx = build_context(tmp_path)
    enable_mcp(ctx)
    text = ctx.services["workflow_brief"](GOAL)
    assert "artifacts_list" in text and "Google Sheet" in text and "No spreadsheet tool" not in text


def test_a_small_goal_gets_nothing_added(tmp_path):
    ctx = build_context(tmp_path)
    assert ctx.services["workflow_brief"]("what is the capital of france") == ""
