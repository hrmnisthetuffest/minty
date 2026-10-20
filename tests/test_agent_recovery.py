"""The agent repairs request-shaped errors instead of failing over (W0-04)."""
from minty_core.agent import Agent
from minty_core.config import ConfigStore
from minty_core.httpclient import error_from_response
from minty_core.keystore import SecretsStore
from minty_core.llm.router import ProviderRouter
from minty_core.llm.types import Message, TextDelta, ToolCall, ToolCallEvent
from minty_core.memory import MemoryStore
from minty_core.personality import PersonalityStore
from minty_core.toolkits import ToolkitRouter
from minty_core.trace import Tracer, read_events
from tests.test_agent_toolkits import Host

TOOL_ERR = ('{"error":{"message":"tool call validation failed: attempted to call tool \'browser_click\' which was not '
            'in request.tools","code":"tool_use_failed"}}')
NAMES = ["clock_get_time", "browser_task", "browser_click", "browser_look", "spotify_play", "smart_home_set_device"]


class Scripted:
    """Each provider answers from its own queue, one entry per call; records what it was sent."""

    def __init__(self, name, queue, log):
        self.name, self.queue, self.log = name, queue, log

    async def stream(self, messages, tools):
        self.log.append({"provider": self.name, "tools": sorted(t.name for t in tools),
                         "chars": sum(len(m.content or "") for m in messages), "messages": list(messages)})
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        for ev in item:
            yield ev


def build(tmp_path, scripts, recover=True):
    store = ConfigStore(tmp_path)
    data = store.load().model_dump(mode="json")
    data["providers"] = [{"name": n, "model": "m"} for n in scripts]
    data["router"]["recover_request_errors"] = recover
    store.update(data)
    secrets = SecretsStore(tmp_path)
    for n in scripts:
        secrets.set(n, "key-" + n)
    log: list[dict] = []
    router = ProviderRouter(store, secrets, lambda cfg, key: Scripted(cfg.name, scripts[cfg.name], log))
    router.tracer = Tracer(tmp_path / "traces")
    personalities = PersonalityStore(tmp_path)
    personalities.ensure_defaults()

    async def confirm(prompt):
        return True

    host = Host(NAMES)
    agent = Agent(router, host, store, personalities, MemoryStore(tmp_path / "m.md"), confirm)
    agent.toolkits = ToolkitRouter(lambda: {p["name"]: p["tools"] for p in host.list_plugins()})
    return agent, host, log, router


def click(name="browser_click"):
    return [ToolCallEvent(ToolCall("c1", name, {"ref": "e3"}))]


async def test_tool_not_offered_loads_the_kit_and_retries_the_same_provider(tmp_path):
    scripts = {"a": [error_from_response("a", 400, {}, TOOL_ERR), click(), [TextDelta("Clicked phones.")]],
               "b": [[TextDelta("never")]]}
    agent, host, log, router = build(tmp_path, scripts)
    out = [s async for s in agent.respond("phones")]
    assert out == ["Clicked phones."]
    assert [c["provider"] for c in log] == ["a", "a", "a"]
    assert "browser_click" not in log[0]["tools"] and "browser_click" in log[1]["tools"]
    assert host.called == ["browser_click"]
    router.tracer.close()
    [rec] = [e for e in read_events(tmp_path / "traces") if e["kind"] == "agent_recovery"]
    assert rec["error_kind"] == "tool_not_offered" and rec["tool"] == "browser_click" and rec["kit"] == "browser"
    assert rec["provider"] == "a"


async def test_request_too_large_trims_and_retries_once(tmp_path):
    big = "x" * 9000
    too_large = error_from_response("a", 413, {}, "Request too large for model on tokens per minute (TPM)")
    scripts = {"a": [too_large, [TextDelta("Done.")]], "b": [[TextDelta("never")]]}
    agent, host, log, router = build(tmp_path, scripts)
    for i in range(3):  # an earlier conversation full of long page readings
        agent.history += [Message("user", f"look {i}"),
                          Message("assistant", "", tool_calls=[ToolCall(f"t{i}", "browser_look", {})]),
                          Message("tool", big, tool_call_id=f"t{i}", name="browser_look"),
                          Message("assistant", "ok")]
    out = [s async for s in agent.respond("what does it say")]
    assert out == ["Done."]
    assert [c["provider"] for c in log] == ["a", "a"]
    assert log[1]["chars"] < log[0]["chars"] / 2
    assert len(log[1]["tools"]) <= len(log[0]["tools"])
    assert log[1]["messages"][-1].content == "what does it say"  # the request itself always survives
    router.tracer.close()
    [rec] = [e for e in read_events(tmp_path / "traces") if e["kind"] == "agent_recovery"]
    assert rec["error_kind"] == "too_large" and rec["chars_after"] < rec["chars_before"]


async def test_a_second_request_error_fails_over_instead_of_looping(tmp_path):
    err = lambda: error_from_response("a", 400, {}, TOOL_ERR)  # noqa: E731
    scripts = {"a": [err(), err()], "b": [[TextDelta("From b.")]]}
    agent, host, log, router = build(tmp_path, scripts)
    out = [s async for s in agent.respond("phones")]
    assert out == ["From b."]
    assert [c["provider"] for c in log] == ["a", "a", "b"]


async def test_unknown_tool_fails_over(tmp_path):
    ghost = ('{"error":{"message":"tool call validation failed: attempted to call tool \'teleport\' which was not in '
             'request.tools"}}')
    scripts = {"a": [error_from_response("a", 400, {}, ghost), [TextDelta("again a")]], "b": [[TextDelta("From b.")]]}
    agent, host, log, router = build(tmp_path, scripts)
    out = [s async for s in agent.respond("teleport me")]
    assert out in (["again a"], ["From b."]) and len(log) == 2


async def test_switched_off_keeps_the_old_failover(tmp_path):
    scripts = {"a": [error_from_response("a", 400, {}, TOOL_ERR)], "b": [[TextDelta("From b.")]]}
    agent, host, log, router = build(tmp_path, scripts, recover=False)
    out = [s async for s in agent.respond("phones")]
    assert out == ["From b."] and [c["provider"] for c in log] == ["a", "b"]


def test_toolkit_router_knows_which_kit_a_tool_belongs_to():
    tk = ToolkitRouter(lambda: {"browser": ["browser_task", "browser_click"], "spotify": ["spotify_play"]})
    assert tk.kit_of("browser_click") == "browser" and tk.kit_of("spotify_play") == "music"
    assert tk.kit_of("browser_task") is None  # core tools belong to no kit
    assert tk.kit_of("nope") is None
    assert tk.load("browser") is True and "browser_click" in tk.tool_names("hello")
    assert tk.load("nope") is False
