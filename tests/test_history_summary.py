"""History summariser (W0-31): older turns of the conversation are sent as one line each, recent ones in full."""
from tests.test_agent_toolkits import make
from minty_core.agent import collapse_history
from minty_core.llm.types import Message, TextDelta, ToolCall


def turn(i, tool=None, reply=None):
    msgs = [Message("user", f"request number {i} about the {'shop' if tool else 'weather'}")]
    if tool:
        msgs += [Message("assistant", "", tool_calls=[ToolCall(f"c{i}", tool, {})]),
                 Message("tool", "result " * 200, tool_call_id=f"c{i}", name=tool)]
    msgs.append(Message("assistant", reply or f"answer number {i} " + "and more words " * 20))
    return msgs


def conversation(n=6):
    out = []
    for i in range(n):
        out += turn(i, tool="browser_click" if i % 2 else None)
    return out


def test_older_turns_become_one_line_each_and_recent_ones_stay_whole():
    history = conversation(6)
    note, recent = collapse_history(history, keep=4)
    assert recent[0].role == "user" and recent[0].content.startswith("request number 2")
    assert [m.content for m in recent] == [m.content for m in history[history.index(recent[0]):]]
    lines = note.splitlines()[1:]
    assert len(lines) == 2 and "request number 0" in lines[0] and "request number 1" in lines[1]
    assert "browser_click" in lines[1] and "answer number 1" in lines[1]
    assert all(len(line) <= 260 for line in lines)


def test_short_conversations_are_untouched():
    history = conversation(3)
    assert collapse_history(history, keep=4) == ("", history)


def test_the_turn_in_progress_keeps_all_its_tool_rounds():
    history = conversation(6) + [Message("user", "click store"),
                                 Message("assistant", "", tool_calls=[ToolCall("x", "browser_click", {})]),
                                 Message("tool", "clicked", tool_call_id="x", name="browser_click")]
    _note, recent = collapse_history(history, keep=1)
    assert [m.role for m in recent] == ["user", "assistant", "tool"]


async def test_agent_sends_the_summary_in_the_system_prompt_and_keeps_its_own_history(tmp_path):
    agent, router, host = make(tmp_path, [[TextDelta("Done.")]])
    sent = []
    original = router.stream

    async def recording(messages, tools, **kw):
        sent.append(list(messages))
        async for ev in original(messages, tools, **kw):
            yield ev

    router.stream = recording
    agent.history = conversation(6)
    full = sum(len(m.content) for m in agent.history)
    [_ async for _ in agent.respond("and the next one")]
    system, *rest = sent[0]
    assert "Earlier in this conversation" in system.content and "request number 0" in system.content
    assert rest[0].content.startswith("request number 3")  # keeps the last 3 earlier turns plus this one
    assert sum(len(m.content) for m in rest) < full
    assert agent.history[0].content.startswith("request number 0")  # nothing is lost from the history itself


async def test_summariser_switched_off(tmp_path):
    agent, router, host = make(tmp_path, [[TextDelta("Done.")]])
    data = agent._store.config.model_dump(mode="json")
    data["turn"]["keep_full_turns"] = 0
    agent._store.update(data)
    sent = []
    original = router.stream

    async def recording(messages, tools, **kw):
        sent.append(list(messages))
        async for ev in original(messages, tools, **kw):
            yield ev

    router.stream = recording
    agent.history = conversation(6)
    [_ async for _ in agent.respond("next")]
    assert sent[0][1].content.startswith("request number 0")


def test_short_turns_are_not_collapsed_when_the_lines_would_be_longer():
    history = []
    for i in range(8):
        history += [Message("user", f"hi {i}"), Message("assistant", "Replayed.")]
    assert collapse_history(history, keep=4) == ("", history)
