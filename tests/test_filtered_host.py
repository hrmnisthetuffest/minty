from minty_core.llm.types import ToolSpec
from minty_core.plugins.host import FilteredHost, Tool


class InnerHost:
    def __init__(self, tools):
        self.tools = {t.name: t for t in tools}
        self.called = []

    def tool_specs(self):
        return [ToolSpec(t.name, t.description, t.parameters) for t in self.tools.values()]

    def get_tool(self, name):
        return self.tools.get(name)

    async def call(self, name, args):
        self.called.append(name)
        return "ran"


def tools():
    obj = {"type": "object", "properties": {}}
    return [Tool("clock_get_time", "t", obj, lambda a: "x"),
            Tool("smart_home_control_secure", "lock", obj, lambda a: "x", risky=True),
            Tool("subagents_spawn_subagent", "spawn", obj, lambda a: "x")]


async def test_hides_risky_and_excluded_plugin_tools():
    inner = InnerHost(tools())
    view = FilteredHost(inner, exclude_plugins=("subagents",))
    assert [s.name for s in view.tool_specs()] == ["clock_get_time"]
    assert view.get_tool("smart_home_control_secure") is None and view.get_tool("subagents_spawn_subagent") is None
    assert await view.call("clock_get_time", {}) == "ran"
    assert (await view.call("smart_home_control_secure", {})).startswith("Error: tool smart_home_control_secure is not available")
    assert inner.called == ["clock_get_time"]


async def test_allow_risky_flag():
    view = FilteredHost(InnerHost(tools()), allow_risky=True)
    assert view.get_tool("smart_home_control_secure") is not None


async def test_unknown_tool_is_not_called_through():
    inner = InnerHost(tools())
    view = FilteredHost(inner)
    assert (await view.call("nope", {})).startswith("Error: tool nope is not available")
    assert inner.called == []


def test_tools_with_dynamic_confirmation_are_hidden_from_helpers():
    obj = {"type": "object", "properties": {}}
    inner = InnerHost([Tool("browser_click", "c", obj, lambda a: "x", confirm_when=lambda a: True),
                       Tool("browser_look", "l", obj, lambda a: "x")])
    view = FilteredHost(inner)
    assert [s.name for s in view.tool_specs()] == ["browser_look"]
    assert view.get_tool("browser_click") is None
