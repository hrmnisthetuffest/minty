from minty_core.agent import Agent
from minty_core.config import ConfigStore
from minty_core.llm.types import TextDelta, ToolCall, ToolCallEvent, ToolSpec
from minty_core.memory import MemoryStore
from minty_core.personality import PersonalityStore
from minty_core.plugins.host import Tool
from minty_core.toolkits import NEED_TOOLKIT, ToolkitRouter

OBJ = {"type": "object", "properties": {}}


class Router:
    def __init__(self, rounds):
        self.rounds, self.offered = list(rounds), []

    async def stream(self, messages, tools, *, sensitive=False, tier="fast"):
        self.offered.append(sorted(t.name for t in tools))
        for ev in self.rounds.pop(0):
            yield ev


class Host:
    def __init__(self, names):
        self.tools = {n: Tool(n, n, OBJ, lambda a: "ok") for n in names}
        self.called = []

    def tool_specs(self):
        return [ToolSpec(t.name, t.description, t.parameters) for t in self.tools.values()]

    def get_tool(self, name):
        return self.tools.get(name)

    async def call(self, name, args):
        self.called.append(name)
        return "tool-result"

    def list_plugins(self):
        plugins = {}
        for name in self.tools:
            plugins.setdefault(name.split("_")[0] if not name.startswith("smart_home") else "smart_home", []).append(name)
        return [{"name": p, "enabled": True, "error": None, "tools": t} for p, t in plugins.items()]


NAMES = ["clock_get_time", "browser_task", "spotify_play", "spotify_pause", "smart_home_set_device"]


def make(tmp_path, rounds, routing=True):
    store = ConfigStore(tmp_path)
    data = store.load().model_dump(mode="json")
    data["toolkit_routing"] = routing
    store.update(data)
    personalities = PersonalityStore(tmp_path)
    personalities.ensure_defaults()

    async def confirm(prompt):
        return True

    router, host = Router(rounds), Host(NAMES)
    agent = Agent(router, host, store, personalities, MemoryStore(tmp_path / "m.md"), confirm)
    agent.toolkits = ToolkitRouter(lambda: {p["name"]: p["tools"] for p in host.list_plugins()})
    return agent, router, host


async def say(agent, text):
    return [s async for s in agent.respond(text)]


async def test_each_request_is_offered_only_its_toolkit(tmp_path):
    agent, router, _ = make(tmp_path, [[TextDelta("Noon.")], [TextDelta("Playing.")]])
    await say(agent, "what time is it")
    await say(agent, "play some jazz")
    assert router.offered[0] == sorted(["clock_get_time", "browser_task", NEED_TOOLKIT])
    assert "spotify_play" in router.offered[1] and "smart_home_set_device" not in router.offered[1]


async def test_need_toolkit_makes_the_tools_available_in_the_next_round(tmp_path):
    ask = ToolCall("1", NEED_TOOLKIT, {"name": "home"})
    use = ToolCall("2", "smart_home_set_device", {})
    agent, router, host = make(tmp_path, [[ToolCallEvent(ask)], [ToolCallEvent(use)], [TextDelta("Done.")]])
    assert await say(agent, "make it cosy in here") == ["Done."]
    assert "smart_home_set_device" not in router.offered[0] and "smart_home_set_device" in router.offered[1]
    assert host.called == ["smart_home_set_device"]
    assert "Loaded home" in next(m.content for m in agent.history if m.name == NEED_TOOLKIT)


async def test_a_tool_that_was_not_offered_still_runs(tmp_path):
    call = ToolCall("1", "spotify_pause", {})
    agent, _, host = make(tmp_path, [[ToolCallEvent(call)], [TextDelta("Paused.")]])
    await say(agent, "hello there")
    assert host.called == ["spotify_pause"]


async def test_routing_off_sends_everything_as_before(tmp_path):
    agent, router, _ = make(tmp_path, [[TextDelta("Hi.")]], routing=False)
    await say(agent, "hello")
    assert router.offered[0] == sorted(NAMES)


async def test_request_specific_prompt_sections_go_last(tmp_path):
    agent, router, _ = make(tmp_path, [[TextDelta("Hi.")]])
    agent.prompt_extras.append(lambda: "\n\nSTABLE")
    agent.turn_extras.append(lambda request: f"\n\nSKILL for {request}")
    prompt = agent._system_prompt("make a lamp")
    assert prompt.index("STABLE") < prompt.index("SKILL for make a lamp") and prompt.endswith("make a lamp")


async def test_an_oversized_tool_result_is_cut_with_an_honest_note(tmp_path):
    from minty_core.agent import MAX_RESULT_CHARS, shape_result

    assert shape_result("short") == "short"
    cut = shape_result("x" * (MAX_RESULT_CHARS + 500))
    assert len(cut) < MAX_RESULT_CHARS + 120 and "500 more characters not shown" in cut
