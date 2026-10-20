"""'use opus on high effort ...': Opus (or any Claude model) only when asked, at the effort named, for one request."""
import pytest

from minty_core.claude_pick import Pick, parse_pick
from minty_core.complexity import classify_level
from minty_core.llm.types import Message


@pytest.mark.parametrize("text, pick, rest", [
    ("use opus", Pick("opus", None), ""),
    ("Use Opus on high effort", Pick("opus", "high"), ""),
    ("use opus on max effort to explain how tides work", Pick("opus", "max"), "explain how tides work"),
    ("please use opus at extra high effort: why is the sky blue", Pick("opus", "xhigh"), "why is the sky blue"),
    ("use sonnet with low effort, what time is it", Pick("sonnet", "low"), "what time is it"),
    ("use haiku on medium effort", Pick("haiku", "medium"), ""),
    ("switch to opus on maximum effort and plan my week", Pick("opus", "max"), "plan my week"),
])
def test_asking_for_a_model_and_an_effort(text, pick, rest):
    assert parse_pick(text) == (pick, rest)


@pytest.mark.parametrize("text", [
    "what is opus", "tell me to use opus sometime", "I use opus a lot", "use opusing", "use a pen on high effort",
    "open chrome", "use the opus magnum as an example",
])
def test_ordinary_sentences_are_left_alone(text):
    assert parse_pick(text) == (None, text)


def msgs(text, tools=0):
    return [Message("user", text)] + [Message("tool", "x") for _ in range(tools)]


@pytest.mark.parametrize("text, tools, level", [
    ("what time is it", 0, "small"), ("play some music", 0, "small"), ("remind me at six to call dad", 0, "small"),
    ("explain why the sky is blue", 0, "medium"), ("write a short message to my teacher about being late", 0, "medium"),
    ("plan a three day trip to Shimla", 0, "large"), ("debug this code for me", 0, "large"),
    ("compare these two phones and tell me which to buy", 0, "large"), ("what is two plus two", 2, "large"),
    ("open the site and then fill the form", 0, "large"),
])
def test_three_levels_of_difficulty(text, tools, level):
    assert classify_level(msgs(text, tools)) == level


# -- the agent: one request, or the next one ------------------------------------------------------------------

from minty_core.agent import Agent  # noqa: E402
from minty_core.config import ConfigStore  # noqa: E402
from minty_core.llm.types import TextDelta  # noqa: E402
from minty_core.memory import MemoryStore  # noqa: E402
from minty_core.personality import PersonalityStore  # noqa: E402
from tests.test_agent import FakeHost, ScriptedRouter  # noqa: E402


class ClaudeRouter(ScriptedRouter):
    def __init__(self, rounds, claude=True):
        super().__init__(rounds)
        self.claude, self.claude_pick, self.armed, self.picks = claude, None, None, []

    async def stream(self, messages, tools, **kw):
        self.picks.append(self.claude_pick)
        async for e in super().stream(messages, tools, **kw):
            yield e

    def has_claude(self):
        return self.claude

    def arm_claude(self, pick):
        self.armed = pick

    def take_armed_claude(self):
        pick, self.armed = self.armed, None
        return pick


def agent_with(tmp_path, router):
    store = ConfigStore(tmp_path)
    store.load()
    personalities = PersonalityStore(tmp_path)
    personalities.ensure_defaults()

    async def confirm(prompt):
        return True

    return Agent(router, FakeHost(), store, personalities, MemoryStore(tmp_path / "m.md"), confirm)


async def say(agent, text):
    return [s async for s in agent.respond(text)]


async def test_use_opus_with_a_request_applies_to_that_request_only(tmp_path):
    router = ClaudeRouter([[TextDelta("Tides come from the moon.")], [TextDelta("Noon.")]])
    agent = agent_with(tmp_path, router)
    assert await say(agent, "use opus on high effort to explain how tides work") == ["Tides come from the moon."]
    assert await say(agent, "what time is it") == ["Noon."]
    assert router.picks == [Pick("opus", "high"), None]
    assert [m.content for m in agent.history if m.role == "user"] == ["explain how tides work", "what time is it"]
    assert router.claude_pick is None


async def test_use_opus_alone_is_for_the_next_request_and_costs_no_model_call(tmp_path):
    router = ClaudeRouter([[TextDelta("Answer.")], [TextDelta("Again.")]])
    agent = agent_with(tmp_path, router)
    reply = await say(agent, "use opus on max effort")
    assert reply == ["Opus on max effort for your next request."] and router.calls == []
    assert await say(agent, "why is the sky blue") == ["Answer."]
    assert await say(agent, "and the sea?") == ["Again."]
    assert router.picks == [Pick("opus", "max"), None]


async def test_without_claude_switched_on_it_says_so_and_changes_nothing(tmp_path):
    router = ClaudeRouter([], claude=False)
    reply = await say(agent_with(tmp_path, router), "use opus on high effort to explain tides")
    assert len(reply) == 1 and "claude-brain on" in reply[0] and router.calls == []


async def test_a_router_that_knows_nothing_about_claude_is_untouched(tmp_path):
    agent = agent_with(tmp_path, ScriptedRouter([[TextDelta("ok.")]]))
    assert await say(agent, "hello") == ["ok."]
