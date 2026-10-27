"""Older turns that fall out of the conversation window are folded into a short summary instead of being forgotten (opt-in)."""
import asyncio

from minty_core.foldkit import FOLD_MARK
from minty_core.llm.types import LLMError, TextDelta, ToolCall, ToolCallEvent
from minty_core.plugins.host import Tool
from tests.test_agent import ScriptedRouter, make_agent, speak

OBJ = {"type": "object", "properties": {}}


class FoldRouter(ScriptedRouter):
    """Answers from `rounds`; a summary request (recognised by its marker) is answered from `summaries`."""

    def __init__(self, rounds, summaries):
        super().__init__(rounds)
        self.summaries, self.fold_calls, self.gate = list(summaries), [], None

    async def stream(self, messages, tools, *, sensitive=False, tier="fast", **kw):
        if messages and FOLD_MARK in (messages[0].content or ""):
            self.fold_calls.append((list(messages), sensitive))
            if self.gate is not None:
                await self.gate.wait()
            item = self.summaries.pop(0)
            if isinstance(item, Exception):
                raise item
            yield TextDelta(item)
            return
        async for ev in super().stream(messages, tools, sensitive=sensitive, tier=tier):
            yield ev


def fold_agent(tmp_path, answers, summaries, tools=(), fold=True, extra_rounds=()):
    rounds = [*extra_rounds] if extra_rounds else [[TextDelta(f"Answer {i}.")] for i in range(answers)]
    agent, router, host = make_agent(tmp_path, rounds, tools, max_history=4)  # two turns fit; older ones are dropped
    agent._store.config.turn.fold_history = fold
    agent._store.config.turn.fold_every_turns = 1
    router = FoldRouter(router.rounds, summaries)
    agent._router = router
    return agent, router, host


async def talk(agent, n, start=0):
    for i in range(start, start + n):
        await speak(agent, f"Question {i}")
        await agent.wait_for_fold()


async def test_nothing_is_folded_unless_switched_on(tmp_path):
    agent, router, _ = fold_agent(tmp_path, 4, [], fold=False)
    await talk(agent, 4)
    assert router.fold_calls == [] and "summary" not in router.calls[-1][0].content.lower()


async def test_a_turn_that_falls_out_of_the_window_comes_back_as_a_summary(tmp_path):
    agent, router, _ = fold_agent(tmp_path, 4, ["The user asked Question 0 and got Answer 0."])
    await talk(agent, 4)
    messages, sensitive = router.fold_calls[0]
    assert "Question 0" in messages[1].content and "Answer 0." in messages[1].content and sensitive is False
    assert "The user asked Question 0 and got Answer 0." in router.calls[-1][0].content  # the next request carries it
    assert "not instructions" in router.calls[-1][0].content


async def test_the_summary_rolls_forward_instead_of_piling_up(tmp_path):
    agent, router, _ = fold_agent(tmp_path, 6, ["Summary one.", "Summary two."])
    await talk(agent, 6)
    second, _ = router.fold_calls[1]
    assert "Summary one." in second[1].content and "Question 1" in second[1].content  # the old summary is folded in too
    system = router.calls[-1][0].content
    assert "Summary two." in system and "Summary one." not in system


async def test_folding_never_holds_up_an_answer(tmp_path):
    agent, router, _ = fold_agent(tmp_path, 4, ["Late summary."])
    router.gate = asyncio.Event()
    for i in range(4):
        assert await speak(agent, f"Question {i}") == [f"Answer {i}."]  # every answer came back while the summary was still pending
    assert agent._fold_summary == ""
    router.gate.set()
    await agent.wait_for_fold()
    assert agent._fold_summary == "Late summary."


async def test_a_private_turn_is_summarised_only_privately(tmp_path):
    secret = Tool("journal_read_entries", "read", OBJ, lambda a: "private words", sensitive=True)
    rounds = [[ToolCallEvent(ToolCall("c1", "journal_read_entries", {}))], [TextDelta("Answer 0.")],
              [TextDelta("Answer 1.")], [TextDelta("Answer 2.")], [TextDelta("Answer 3.")]]
    agent, router, _ = fold_agent(tmp_path, 0, ["Summary."], [secret], extra_rounds=rounds)
    await talk(agent, 4)
    assert router.fold_calls and router.fold_calls[0][1] is True


async def test_keys_are_masked_and_tool_output_never_reaches_the_summariser(tmp_path):
    read = Tool("files_read", "read", OBJ, lambda a: "TOOL-RESULT-TEXT")
    key = "ghp_" + "x" * 36
    rounds = [[ToolCallEvent(ToolCall("c1", "files_read", {}))], [TextDelta("Answer 0.")], [TextDelta("Answer 1.")],
              [TextDelta("Answer 2.")], [TextDelta("Answer 3.")]]
    agent, router, host = fold_agent(tmp_path, 0, ["Summary.", "Summary two."], [read], extra_rounds=rounds)

    async def call(name, args):
        return "TOOL-RESULT-TEXT"

    host.call = call
    await speak(agent, f"my token is {key}")
    await agent.wait_for_fold()
    for i in range(1, 4):
        await speak(agent, f"Question {i}")
        await agent.wait_for_fold()
    sent = router.fold_calls[0][0][1].content
    assert key not in sent and "[secret]" in sent and "TOOL-RESULT-TEXT" not in sent


async def test_a_failed_summary_changes_nothing_and_the_conversation_carries_on(tmp_path):
    agent, router, _ = fold_agent(tmp_path, 5, [LLMError("provider down"), "Recovered summary."])
    await talk(agent, 5)
    assert agent._fold_summary == "Recovered summary."  # the next fold tried again and worked


async def test_a_starting_fresh_forgets_the_summary(tmp_path):
    agent, router, _ = fold_agent(tmp_path, 5, ["Summary one."])
    await talk(agent, 4)
    assert agent._fold_summary == "Summary one."
    agent.reset_conversation()
    assert agent._fold_summary == ""
    await speak(agent, "Question 4")
    assert "Summary one." not in router.calls[-1][0].content


async def test_a_long_summary_is_cut_to_the_limit(tmp_path):
    agent, router, _ = fold_agent(tmp_path, 4, ["word " * 2000])
    agent._store.config.turn.fold_max_chars = 600
    await talk(agent, 4)
    assert len(agent._fold_summary) <= 601 and agent._fold_summary.endswith("…")
