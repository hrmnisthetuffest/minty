"""Shared pieces for the safety-layer tests: an agent whose model calls the tools it is told to."""
from minty_core.agent import Agent
from minty_core.config import ConfigStore
from minty_core.llm.types import TextDelta, ToolCall, ToolCallEvent, ToolSpec
from minty_core.memory import MemoryStore
from minty_core.personality import PersonalityStore
from minty_core.plugins.host import Tool

OBJ = {"type": "object", "properties": {}}


class Host:
    """tools: name -> (risk, fn). Records what really ran."""

    def __init__(self, tools):
        self.tools = {n: Tool(n, n, OBJ, lambda a: "ok", risk=risk, egress=egress)
                      for n, (risk, egress) in tools.items()}
        self.called: list[tuple[str, dict]] = []
        self.results: dict[str, str] = {}

    def tool_specs(self):
        return [ToolSpec(t.name, t.description, t.parameters) for t in self.tools.values()]

    def get_tool(self, name):
        return self.tools.get(name)

    async def call(self, name, args):
        self.called.append((name, args))
        return self.results.get(name, f"{name} done")

    def list_plugins(self):
        return [{"name": "t", "enabled": True, "error": None, "tools": list(self.tools)}]


class Router:
    """Each round is a list of events; the last round should be text."""

    def __init__(self, rounds):
        self.rounds = list(rounds)

    async def stream(self, messages, tools, **kw):
        for ev in self.rounds.pop(0) if self.rounds else [TextDelta("Done.")]:
            yield ev


def call(name, args=None, ident="1"):
    return ToolCallEvent(ToolCall(ident, name, args or {}))


def build(tmp_path, tools, rounds, confirm_answer=True, settings=None, safety=None):
    store = ConfigStore(tmp_path)
    data = store.load().model_dump(mode="json")
    data["toolkit_routing"] = False
    for section, values in (settings or {}).items():
        data[section].update(values)
    store.update(data)
    personalities = PersonalityStore(tmp_path)
    personalities.ensure_defaults()
    asked: list[str] = []

    async def confirm(prompt):
        asked.append(prompt)
        return confirm_answer

    host = Host(tools)
    agent = Agent(Router(rounds), host, store, personalities, MemoryStore(tmp_path / "m.md"), confirm)
    if safety is not None:
        agent.safety = safety(store)
    return agent, host, asked


async def say(agent, text):
    return [s async for s in agent.respond(text)]
