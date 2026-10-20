"""Residency labels (LF-15): what may reach a model, enforced by the agent rather than by each tool."""
from minty_core.agent import Agent
from minty_core.config import ConfigStore
from minty_core.llm.types import TextDelta, ToolCall, ToolCallEvent, ToolSpec
from minty_core.memory import MemoryStore
from minty_core.personality import PersonalityStore
from minty_core.plugins.host import Tool
from minty_core.residency import FINANCIAL_LOCAL, LOCAL_ONLY, NORMAL, SENSITIVE, may_enter_prompt, residency_of

PLANTED = "Strava run 2026-10-12: 10.4 km in 52:13, avg HR 161, route Sukhna Lake"
OBJ = {"type": "object", "properties": {}}


class Host:
    def __init__(self):
        self.tools = {
            "health_strava_recent": Tool("health_strava_recent", "Recent Strava runs", OBJ, lambda a: PLANTED,
                                         sensitive=True, local_only=True),
            "health_summary": Tool("health_summary", "Weekly facts pack", OBJ, lambda a: "7 days: 3 workouts",
                                   sensitive=True),
        }

    def tool_specs(self):
        return [ToolSpec(t.name, t.description, t.parameters) for t in self.tools.values()]

    def get_tool(self, name):
        return self.tools.get(name)

    async def call(self, name, args):
        return self.tools[name].func(args)

    def list_plugins(self):
        return [{"name": "health", "enabled": True, "error": None, "tools": list(self.tools)}]


class Recording:
    """A model that records every message it is ever sent, on every call path."""

    def __init__(self, rounds):
        self.rounds, self.sent, self.sensitive = list(rounds), [], []

    async def stream(self, messages, tools, **kw):
        self.sensitive.append(kw.get("sensitive"))
        self.sent.append("\n".join(f"{m.role}: {m.content}" for m in messages))
        for ev in self.rounds.pop(0) if self.rounds else [TextDelta("I don't have that in front of me.")]:
            yield ev


def build(tmp_path, rounds):
    store = ConfigStore(tmp_path)
    data = store.load().model_dump(mode="json")
    data["toolkit_routing"] = False
    store.update(data)
    personalities = PersonalityStore(tmp_path)
    personalities.ensure_defaults()
    router = Recording(rounds)

    async def confirm(prompt):
        return True

    agent = Agent(router, Host(), store, personalities, MemoryStore(tmp_path / "m.md"), confirm)
    return agent, router


def test_origins_map_to_classes_and_unknown_origins_fail_closed():
    assert residency_of("strava_api") == LOCAL_ONLY
    assert {residency_of(o) for o in ("hevy", "cronometer_csv", "health_connect", "strava_export")} == {SENSITIVE}
    assert residency_of("finance_csv") == FINANCIAL_LOCAL and residency_of("chat") == NORMAL
    assert residency_of("some_new_exporter") == SENSITIVE
    assert may_enter_prompt(LOCAL_ONLY, as_facts=True) is False
    assert may_enter_prompt(SENSITIVE, as_facts=False) is False and may_enter_prompt(SENSITIVE, as_facts=True) is True
    assert may_enter_prompt(NORMAL, as_facts=False) is True


async def test_a_local_only_result_goes_to_you_and_to_no_model(tmp_path):
    agent, router = build(tmp_path, [[ToolCallEvent(ToolCall("1", "health_strava_recent", {}))]])
    out = [s async for s in agent.respond("how were my last runs")]
    assert PLANTED in "".join(out)  # you see it
    assert len(router.sent) == 1 and PLANTED not in router.sent[0]  # the model was never given it
    assert all(PLANTED not in m.content for m in agent.history)


async def test_it_stays_out_of_every_later_request_the_hooks_and_the_archive(tmp_path):
    agent, router = build(tmp_path, [[ToolCallEvent(ToolCall("1", "health_strava_recent", {}))]])
    heard, logged = [], []
    agent.turn_done.append(lambda request, reply: heard.append(reply))

    class Archive:
        def log(self, role, text, private=False):
            logged.append((role, text, private))

    agent.archive = Archive()
    [s async for s in agent.respond("how were my last runs")]
    [s async for s in agent.respond("and the one before that?")]
    assert all(PLANTED not in sent for sent in router.sent)
    assert PLANTED not in " ".join(heard) and all(PLANTED not in text for _, text, _ in logged)
    assert router.sensitive == [False, True]  # the later question went only to providers that allow private turns


async def test_a_facts_pack_may_reach_a_model_that_allows_private_turns(tmp_path):
    agent, router = build(tmp_path, [[ToolCallEvent(ToolCall("1", "health_summary", {}))],
                                     [TextDelta("Three sessions this week.")]])
    out = "".join([s async for s in agent.respond("how was my week")])
    assert out == "Three sessions this week." and "7 days: 3 workouts" in router.sent[1]
