"""Independent read-only lookups in one round run together; anything that changes something is a barrier."""
import asyncio
import time

import pytest

from minty_core.llm.types import TextDelta, ToolCall, ToolCallEvent
from minty_core.plugins.host import PARALLEL_SAFE_TOOLS, Tool
from tests.test_agent import make_agent, speak

OBJ = {"type": "object", "properties": {}}
SLOW = 0.25


def reads():
    return [Tool("weather_get_weather", "weather", OBJ, lambda a: "w"), Tool("web_search_search", "search", OBJ, lambda a: "s"),
            Tool("clock_get_time", "time", OBJ, lambda a: "t")]


def timed(agent, host, delays=None):
    log = []
    delays = delays or {}

    async def call(name, args):
        log.append(("start", name, time.perf_counter()))
        await asyncio.sleep(delays.get(name, SLOW))
        log.append(("end", name, time.perf_counter()))
        host.called.append((name, args))
        return f"result of {name}"

    host.call = call
    return log


def call_event(i, name, **args):
    return ToolCallEvent(ToolCall(f"c{i}", name, args))


def span(log, name):
    start = next(t for k, n, t in log if k == "start" and n == name)
    end = next(t for k, n, t in log if k == "end" and n == name)
    return start, end


def test_the_allow_list_holds_only_stateless_lookups():
    assert {"weather_get_weather", "web_search_search", "clock_get_time"} <= PARALLEL_SAFE_TOOLS
    assert not any(n.startswith(("browser_", "mail_", "files_write", "files_delete", "installer_")) for n in PARALLEL_SAFE_TOOLS)


async def test_two_lookups_in_one_round_run_at_the_same_time_and_answer_in_order(tmp_path):
    rounds = [[call_event(1, "weather_get_weather"), call_event(2, "web_search_search")], [TextDelta("Both done.")]]
    agent, _, host = make_agent(tmp_path, rounds, reads())
    log = timed(agent, host)
    started = time.perf_counter()
    assert await speak(agent) == ["Both done."]
    assert time.perf_counter() - started < SLOW * 1.7  # one wait, not two
    tools = [m for m in agent.history if m.role == "tool"]
    assert [(m.tool_call_id, m.name) for m in tools] == [("c1", "weather_get_weather"), ("c2", "web_search_search")]
    assert [m.content for m in tools] == ["result of weather_get_weather", "result of web_search_search"]


async def test_the_switch_turns_it_off(tmp_path):
    rounds = [[call_event(1, "weather_get_weather"), call_event(2, "web_search_search")], [TextDelta("Done.")]]
    agent, _, host = make_agent(tmp_path, rounds, reads())
    agent._store.config.turn.parallel_reads = False
    timed(agent, host)
    started = time.perf_counter()
    await speak(agent)
    assert time.perf_counter() - started >= SLOW * 2 - 0.02


async def test_a_change_is_a_barrier_and_the_user_is_asked_only_after_the_reads(tmp_path):
    send = Tool("mail_send", "send", OBJ, lambda a: "sent", risky=True)
    rounds = [[call_event(1, "weather_get_weather"), call_event(2, "web_search_search"), call_event(3, "mail_send", to="sam"),
               call_event(4, "clock_get_time")], [TextDelta("Done.")]]
    agent, _, host = make_agent(tmp_path, rounds, reads() + [send])
    log = timed(agent, host, {"mail_send": 0.05})
    asked = []

    async def confirm(prompt):
        asked.append(time.perf_counter())
        return True

    agent.confirm = confirm
    await speak(agent)
    a0, a1 = span(log, "weather_get_weather")
    b0, b1 = span(log, "web_search_search")
    w0, w1 = span(log, "mail_send")
    c0, c1 = span(log, "clock_get_time")
    assert abs(a0 - b0) < 0.1 and a0 < b1 and b0 < a1  # the first two overlapped
    assert len(asked) == 1 and asked[0] >= max(a1, b1) - 0.01  # one question, asked after both reads had finished
    assert w0 >= max(a1, b1) - 0.01 and c0 >= w1 - 0.01  # the write waited for them, the next read waited for the write
    assert [m.tool_call_id for m in agent.history if m.role == "tool"] == ["c1", "c2", "c3", "c4"]


async def test_a_tool_that_is_not_on_the_list_stays_one_at_a_time(tmp_path):
    scroll = Tool("browser_scroll", "scroll", OBJ, lambda a: "ok")  # a "read" that moves the page: not safe to overlap
    rounds = [[call_event(1, "browser_scroll"), call_event(2, "weather_get_weather"), call_event(3, "browser_scroll")],
              [TextDelta("Done.")]]
    agent, _, host = make_agent(tmp_path, rounds, reads() + [scroll])
    log = timed(agent, host)
    await speak(agent)
    s0, s1 = span(log, "browser_scroll")
    w0, w1 = span(log, "weather_get_weather")
    assert w0 >= s1 - 0.01  # it did not start until the scroll before it had finished


async def test_results_are_noted_in_the_order_the_calls_were_made_not_the_order_they_finished(tmp_path):
    class Noted:
        flagged, sources = False, []
        def __init__(self): self.order = []
        def reset(self): pass
        def note_result(self, name, result): self.order.append(name)
        def tainted(self, args, text): return []
        def note_for(self, *a, **k): return ""
        def extra_risk(self, *a, **k): return 0

    rounds = [[call_event(1, "weather_get_weather"), call_event(2, "web_search_search")], [TextDelta("Done.")]]
    agent, _, host = make_agent(tmp_path, rounds, reads())
    agent.provenance = Noted()
    timed(agent, host, {"weather_get_weather": 0.2, "web_search_search": 0.01})  # the second one finishes first
    await speak(agent)
    assert agent.provenance.order == ["weather_get_weather", "web_search_search"]


async def test_the_tool_indicator_changes_once_around_the_whole_batch(tmp_path):
    seen = []
    rounds = [[call_event(1, "weather_get_weather"), call_event(2, "web_search_search")], [TextDelta("Done.")]]
    agent, _, host = make_agent(tmp_path, rounds, reads())
    agent.on_tool = seen.append
    timed(agent, host)
    await speak(agent)
    assert seen.count(None) == 1 and len(seen) == 2


async def test_an_error_in_one_lookup_does_not_spoil_the_other(tmp_path):
    rounds = [[call_event(1, "weather_get_weather"), call_event(2, "web_search_search")], [TextDelta("Partly.")]]
    agent, _, host = make_agent(tmp_path, rounds, reads())

    async def call(name, args):
        return "Error: no signal" if name == "weather_get_weather" else "found it"

    host.call = call
    assert await speak(agent) == ["Partly."]
    assert [m.content for m in agent.history if m.role == "tool"] == ["Error: no signal", "found it"]


async def test_an_exception_in_a_lookup_ends_the_turn_and_rolls_back_like_before(tmp_path):
    rounds = [[call_event(1, "weather_get_weather"), call_event(2, "web_search_search")], [TextDelta("Done.")]]
    agent, _, host = make_agent(tmp_path, rounds, reads())

    async def call(name, args):
        if name == "web_search_search":
            raise RuntimeError("boom")
        await asyncio.sleep(0.05)
        return "ok"

    host.call = call
    with pytest.raises(RuntimeError):
        await speak(agent)
    assert agent.history == []


async def test_a_malformed_call_in_the_batch_never_runs(tmp_path):
    strict = Tool("web_search_search", "search", {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}},
                  lambda a: "s")
    rounds = [[call_event(1, "weather_get_weather"), call_event(2, "web_search_search")], [TextDelta("Done.")]]
    agent, _, host = make_agent(tmp_path, rounds, [reads()[0], strict])
    timed(agent, host)
    await speak(agent)
    assert [n for n, _ in host.called] == ["weather_get_weather"]
    assert [m.content.startswith("Error:") for m in agent.history if m.role == "tool"] == [False, True]

