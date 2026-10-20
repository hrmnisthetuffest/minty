"""A tool that keeps failing stops being offered for a while, so it stops costing a model round trip each time."""
from minty_core.agent import Agent
from minty_core.config import ConfigStore
from minty_core.llm.types import TextDelta, ToolCall, ToolCallEvent, ToolSpec
from minty_core.memory import MemoryStore
from minty_core.personality import PersonalityStore
from minty_core.plugins.host import Tool
from minty_core.toolbreaker import ToolBreaker


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_three_failures_in_a_row_open_it_and_a_success_resets_the_count():
    clock = Clock()
    b = ToolBreaker(threshold=3, rest_s=600, clock=clock)
    assert b.record("mail_unread", failed=True) is False and b.record("mail_unread", failed=True) is False
    assert b.record("mail_unread", failed=False) is False  # a success: start counting again
    assert b.record("mail_unread", failed=True) is False and b.record("mail_unread", failed=True) is False
    assert b.record("mail_unread", failed=True) is True  # the third in a row trips it
    assert b.resting() == {"mail_unread"}
    assert b.record("clock_get_time", failed=True) is False and "clock_get_time" not in b.resting()  # per tool


def test_it_rests_for_a_while_then_gets_one_more_try_and_a_failure_reopens_it_at_once():
    clock = Clock()
    b = ToolBreaker(threshold=2, rest_s=600, clock=clock)
    b.record("t", failed=True)
    assert b.record("t", failed=True) is True and b.resting() == {"t"}
    clock.now += 599
    assert b.resting() == {"t"}
    clock.now += 2  # rested: offered again
    assert b.resting() == set()
    assert b.record("t", failed=True) is True and b.resting() == {"t"}  # still broken: back to resting straight away
    clock.now += 700
    assert b.record("t", failed=False) is False and b.resting() == set()  # it works again: fully reset
    assert b.record("t", failed=True) is False  # the count started over


def test_the_note_names_the_resting_tools_and_is_empty_when_all_is_well():
    b = ToolBreaker(threshold=1, rest_s=60, clock=Clock())
    assert b.note() == ""
    b.record("mail_unread", failed=True)
    assert "mail_unread" in b.note() and "unavailable" in b.note().lower()


class Router:
    def __init__(self, rounds):
        self.rounds, self.offered, self.systems = list(rounds), [], []

    async def stream(self, messages, tools, *, sensitive=False, tier="fast"):
        self.offered.append([t.name for t in tools])
        self.systems.append(messages[0].content)
        for ev in self.rounds.pop(0):
            yield ev


class BrokenHost:
    def __init__(self, tools):
        self.tools = {t.name: t for t in tools}

    def tool_specs(self):
        return [ToolSpec(t.name, t.description, t.parameters) for t in self.tools.values()]

    def get_tool(self, name):
        return self.tools.get(name)

    async def call(self, name, args):
        return "Error: the mail server is not reachable" if name == "mail_unread" else "ok"


async def test_the_agent_stops_offering_a_tool_that_failed_three_times_and_tells_the_model(tmp_path):
    store = ConfigStore(tmp_path)
    store.load()
    personalities = PersonalityStore(tmp_path)
    personalities.ensure_defaults()
    mail = Tool("mail_unread", "unread mail", {"type": "object", "properties": {}}, lambda a: "x")
    clock = Tool("clock_get_time", "time", {"type": "object", "properties": {}}, lambda a: "x")
    rounds = []
    for i in range(3):  # three turns, each: the model calls the mail tool, then answers
        rounds += [[ToolCallEvent(ToolCall(f"c{i}", "mail_unread", {}))], [TextDelta("Mail is down.")]]
    rounds.append([TextDelta("Nothing to report.")])  # the fourth turn
    router = Router(rounds)

    async def confirm(prompt):
        return True

    agent = Agent(router, BrokenHost([mail, clock]), store, personalities, MemoryStore(tmp_path / "memory.md"), confirm)
    for _ in range(4):
        [s async for s in agent.respond("any unread mail")]
    assert "mail_unread" in router.offered[0]  # offered while it works
    assert "mail_unread" not in router.offered[-1] and "clock_get_time" in router.offered[-1]  # then rested
    assert "mail_unread" in router.systems[-1] and "unavailable" in router.systems[-1].lower()
