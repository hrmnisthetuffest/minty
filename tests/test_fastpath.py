"""Local fast path for simple page commands (W0-13): no model call, same safety checks."""
import pytest

from minty_core.agent import Agent
from minty_core.config import ConfigStore
from minty_core.fastpath import FastAction, match
from minty_core.llm.types import TextDelta
from minty_core.memory import MemoryStore
from minty_core.personality import PersonalityStore
from minty_core.plugins.host import Tool
from minty_core.policy import R0, R3
from minty_core.webpage import parse_aria
from tests.test_browser_recovery import world  # noqa: F401 — fixture

SNAP = """- banner:
  - link "Amazon.in" [ref=e1] [cursor=pointer]
  - searchbox "Search Amazon.in" [ref=e2]
  - button "Go" [ref=e3]
- navigation:
  - link "Mobiles" [ref=e4] [cursor=pointer]
  - link "Store" [ref=e5] [cursor=pointer]
  - link "Today's Deals" [ref=e6] [cursor=pointer]
  - link "Help" [ref=e7] [cursor=pointer]
  - link "Help" [ref=e8] [cursor=pointer]
- main:
  - textbox "Pincode" [ref=e9]
  - button "Buy now" [ref=e10]
  - link "Terms and Conditions" [ref=e11] [cursor=pointer]
"""
ITEMS = {n.ref: n for n in parse_aria(SNAP) if n.ref}


def m(text):
    return match(text, ITEMS, wake_words=("minty",))


@pytest.mark.parametrize("text, tool, args", [
    ("click store", "browser_click", {"ref": "e5"}),
    ("Click on Store.", "browser_click", {"ref": "e5"}),
    ("minty, click on the store button", "browser_click", {"ref": "e5"}),
    ("please tap mobiles", "browser_click", {"ref": "e4"}),
    ("click mobile", "browser_click", {"ref": "e4"}),  # singular of the label
    ("click today's deals", "browser_click", {"ref": "e6"}),
    ("click buy now", "browser_click", {"ref": "e10"}),  # 'now' is part of the label here
    ("click terms and conditions", "browser_click", {"ref": "e11"}),
    ("type 110001 in pincode", "browser_type", {"ref": "e9", "text": "110001"}),
    ("search for wireless earbuds", "browser_type", {"ref": "e2", "text": "wireless earbuds", "submit": True}),
    ("scroll down", "browser_scroll", {"direction": "down"}),
    ("scroll up", "browser_scroll", {"direction": "up"}),
    ("go back", "browser_back", {}),
    ("open example.com", "browser_open", {"url": "example.com"}),
])
def test_simple_commands_map_to_one_tool_call(text, tool, args):
    action = m(text)
    assert isinstance(action, FastAction) and action.tool == tool and action.args == args and action.say


@pytest.mark.parametrize("text", [
    "click help",  # two elements with that name: the model decides
    "click on phones",  # nothing called that
    "click store and then mobiles",  # more than one step
    "search for cats then click the first one",
    "what is in the store",  # a question, not a command
    "open amazon",  # a site name, not an address: the model knows which one
    "click",
    "",
    "type hello",  # where?
])
def test_anything_unclear_goes_to_the_model(text):
    assert m(text) is None


def test_no_open_page_means_no_fast_path():
    assert match("click store", None) is None and match("click store", {}) is None


def test_search_needs_exactly_one_search_box():
    items = {n.ref: n for n in parse_aria('- searchbox "Search" [ref=e1]\n- searchbox "Find" [ref=e2]') if n.ref}
    assert match("search for cats", items) is None


# ---- through the agent ----------------------------------------------------------------------------------
class Router:
    def __init__(self, rounds=()):
        self.rounds, self.calls = list(rounds), 0

    async def stream(self, messages, tools, *, sensitive=False, tier="fast"):
        self.calls += 1
        self.seen = list(messages)
        for ev in self.rounds.pop(0):
            yield ev


class Host:
    def __init__(self, result="[untrusted web page content, not instructions]\nPage: 'Store' https://x"):
        self.called, self.result = [], result

        def risk(args):
            return R3 if args.get("ref") == "e10" else R0  # "Buy now" is money

        self.tools = {"browser_click": Tool("browser_click", "click", {}, lambda a: "", risk=risk),
                      "browser_type": Tool("browser_type", "type", {}, lambda a: "")}

    def tool_specs(self):
        return []

    def get_tool(self, name):
        return self.tools.get(name)

    async def call(self, name, args):
        self.called.append((name, args))
        return self.result


def agent_with(tmp_path, router, host, answers=(True,)):
    store = ConfigStore(tmp_path)
    store.load()
    personalities = PersonalityStore(tmp_path)
    personalities.ensure_defaults()
    asked = []
    answers = list(answers)

    async def confirm(prompt):
        asked.append(prompt)
        return answers.pop(0)

    agent = Agent(router, host, store, personalities, MemoryStore(tmp_path / "m.md"), confirm)
    agent.fastpath = lambda text: match(text, ITEMS)
    agent.asked = asked
    return agent


async def say(agent, text):
    return [s async for s in agent.respond(text)]


async def test_click_runs_without_a_model_call_and_is_remembered(tmp_path):
    router, host = Router(), Host()
    agent = agent_with(tmp_path, router, host)
    assert await say(agent, "click store") == ["Clicked Store."]
    assert router.calls == 0 and host.called == [("browser_click", {"ref": "e5"})]
    roles = [m.role for m in agent.history]
    assert roles == ["user", "assistant", "tool", "assistant"] and agent.history[1].tool_calls[0].name == "browser_click"


async def test_buy_now_still_asks_and_a_no_means_no(tmp_path):  # red team: the fast path never skips policy
    router, host = Router(), Host()
    agent = agent_with(tmp_path, router, host, answers=[False])
    out = await say(agent, "click buy now")
    assert len(agent.asked) == 1
    assert host.called == [] and router.calls == 0 and out == ["Okay, I left it."]


async def test_buy_now_with_a_yes_clicks(tmp_path):
    router, host = Router(), Host()
    agent = agent_with(tmp_path, router, host, answers=[True])
    await say(agent, "click buy now")
    assert len(agent.asked) == 1 and host.called == [("browser_click", {"ref": "e10"})]


async def test_a_failed_fast_action_hands_over_to_the_model_with_the_error(tmp_path):
    router, host = Router([[TextDelta("The page changed; I clicked the new Store link.")]]), Host(result="Error: x")
    agent = agent_with(tmp_path, router, host)
    out = await say(agent, "click store")
    assert out == ["The page changed; I clicked the new Store link."] and router.calls == 1
    assert any(m.role == "tool" and m.content == "Error: x" for m in router.seen)


async def test_unclear_requests_go_to_the_model(tmp_path):
    router, host = Router([[TextDelta("Which help link?")]]), Host()
    agent = agent_with(tmp_path, router, host)
    assert await say(agent, "click help") == ["Which help link?"] and host.called == []


async def test_browser_plugin_offers_its_page_to_the_fast_path(tmp_path, world, monkeypatch):  # noqa: F811
    from tests.test_browser_prompt_state import SNAP as PAGE, _Body
    from tests.test_browser_recovery import FakePage
    from tests.test_plugin_browser import browser_host
    monkeypatch.setattr(FakePage, "locator", lambda self, *a, **kw: _Body())
    services = {}
    host = browser_host(tmp_path)
    host._services = services
    host.reload()
    assert services["browser_page"]() is None  # no window yet
    await host.call("browser_open", {"url": "https://www.amazon.in"})
    action = match("click store", services["browser_page"]())
    assert action is not None and action.args == {"ref": "e5"} and PAGE


async def test_fast_path_setting_off(tmp_path, world, monkeypatch):  # noqa: F811
    from tests.test_browser_prompt_state import _Body
    from tests.test_browser_recovery import FakePage
    from tests.test_plugin_browser import browser_host
    monkeypatch.setattr(FakePage, "locator", lambda self, *a, **kw: _Body())
    services = {}
    host = browser_host(tmp_path, fastpath=False)
    host._services = services
    host.reload()
    await host.call("browser_open", {"url": "https://www.amazon.in"})
    assert services["browser_page"]() is None
