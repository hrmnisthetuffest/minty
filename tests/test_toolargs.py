import pytest

from minty_core.agent import Agent
from minty_core.llm.types import TextDelta, ToolCall, ToolCallEvent
from minty_core.plugins.host import Tool
from minty_core.toolargs import check_args
from tests.test_agent import make_agent, speak

SCHEMA = {"type": "object", "required": ["query"], "properties": {
    "query": {"type": "string"}, "limit": {"type": "integer"}, "exact": {"type": "boolean"},
    "ratio": {"type": "number"}, "tags": {"type": "array"}, "mode": {"type": "string", "enum": ["fast", "deep"]}}}


def tool(schema=SCHEMA):
    return Tool("t_find", "find", schema, lambda a: "ok")


def test_good_arguments_pass_unchanged():
    args, err = check_args(tool(), {"query": "cats", "limit": 3, "exact": True, "ratio": 0.5, "tags": ["a"], "mode": "fast"})
    assert err is None and args == {"query": "cats", "limit": 3, "exact": True, "ratio": 0.5, "tags": ["a"], "mode": "fast"}


def test_a_missing_required_argument_is_named():
    _, err = check_args(tool(), {"limit": 3})
    assert err.startswith("Error:") and "query" in err and "t_find" in err


@pytest.mark.parametrize("given,expected", [("5", 5), ("  7 ", 7), (5.0, 5)])
def test_whole_numbers_given_as_text_are_repaired_not_rejected(given, expected):
    args, err = check_args(tool(), {"query": "x", "limit": given})
    assert err is None and args["limit"] == expected and type(args["limit"]) is int


def test_booleans_and_floats_given_as_text_are_repaired():
    args, err = check_args(tool(), {"query": "x", "exact": "true", "ratio": "0.25"})
    assert err is None and args["exact"] is True and args["ratio"] == 0.25


@pytest.mark.parametrize("bad", [{"query": "x", "limit": "many"}, {"query": "x", "limit": 2.5}, {"query": "x", "limit": True},
                                 {"query": "x", "exact": "maybe"}, {"query": "x", "tags": "a,b"}, {"query": 7},
                                 {"query": "x", "mode": "slow"}])
def test_clearly_wrong_values_are_rejected_with_a_fixable_message(bad):
    args, err = check_args(tool(), bad)
    assert err.startswith("Error:") and "call it again" in err


def test_unknown_extra_arguments_are_left_alone_and_odd_schemas_are_skipped():
    args, err = check_args(tool(), {"query": "x", "extra": 1})
    assert err is None and args["extra"] == 1
    for odd in ({}, {"type": "object"}, {"type": "object", "properties": {"a": {"type": ["string", "null"]}}}):
        assert check_args(tool(odd), {"a": 5})[1] is None
    assert check_args(tool(), "not a dict")[1].startswith("Error:")


async def test_a_bad_call_never_reaches_the_tool_or_the_user_and_gets_one_repair(tmp_path):
    risky = Tool("t_find", "find", SCHEMA, lambda a: "ok", risky=True)
    rounds = [[ToolCallEvent(ToolCall("c1", "t_find", {"limit": 2}))],
              [ToolCallEvent(ToolCall("c2", "t_find", {"query": "cats", "limit": "3"}))], [TextDelta("Found it.")]]
    asked = []
    agent, router, host = make_agent(tmp_path, rounds, [risky])

    async def confirm(prompt):
        asked.append(prompt)
        return True

    agent.confirm = confirm
    assert await speak(agent) == ["Found it."]
    assert host.called == [("t_find", {"query": "cats", "limit": 3})]  # only the repaired call ran, with the number fixed
    assert len(asked) == 1  # the malformed call never asked the user to approve anything
    bad = [m for m in agent.history if m.role == "tool"][0]
    assert bad.tool_call_id == "c1" and bad.content.startswith("Error:") and "query" in bad.content


async def test_bad_arguments_count_toward_the_loop_guard(tmp_path):
    rounds = [[ToolCallEvent(ToolCall(f"c{i}", "t_find", {"limit": i}))] for i in range(6)]
    agent, router, host = make_agent(tmp_path, rounds, [tool()], max_rounds=8)
    out = await speak(agent)
    assert host.called == [] and "going in circles" in out[-1]
