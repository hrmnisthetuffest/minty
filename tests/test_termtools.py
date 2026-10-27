"""Minty prefers a command-line tool when one fits: it is told which ones this computer has, only when a request could use them."""
from minty_core import termtools
from minty_core.context import build_context

HAVE = {"ddgr", "git", "gh", "instagram-cli"}


def which(name):
    return f"/usr/bin/{name}" if name in HAVE else None


def test_only_the_tools_that_are_installed_are_listed_in_order():
    assert [t[0] for t in termtools.found(which)] == ["ddgr", "gh", "git", "instagram-cli"]
    assert termtools.found(lambda name: None) == []


def test_a_request_that_could_use_a_tool_gets_a_short_hint_that_names_how_to_use_it():
    text = termtools.hint("search for the arch wiki and open it", which)
    assert "ddgr --json" in text and "ddgr -j" in text and "instagram-cli" in text
    assert len(text) < 900  # it rides along on a request, so it stays short
    assert "never type a password" in text.lower() and "logs in" in text.lower() and "asked first" in text.lower()
    assert "jq" not in text and "rg" not in text.split("(")[0]  # tools this computer does not have are not mentioned


def test_an_unrelated_request_gets_nothing():
    for request in ("how are you today", "what is twelve times eight", "tell me a joke", ""):
        assert termtools.hint(request, which) == ""


def test_a_short_tool_name_inside_another_word_is_not_a_mention():
    for request in ("what is eight times nine", "the rgb colours", "a jqx thing", "the weight of the ghost"):
        assert termtools.hint(request, which) == "", request
    assert "gh" in termtools.hint("use gh please", which)


def test_naming_a_tool_is_enough():
    assert "instagram-cli" in termtools.hint("check my insta inbox", which)
    assert "gh " in termtools.hint("list my github issues", which) or "gh (" in termtools.hint("list my github issues", which)


def test_nothing_is_said_when_no_tool_is_installed():
    assert termtools.hint("search the web and open it", lambda name: None) == ""


async def test_the_hint_rides_on_the_request_only_while_the_terminal_is_on(tmp_path, monkeypatch):
    monkeypatch.setattr(termtools, "found", lambda which=None: [t for t in termtools.TOOLS if t[0] in ("ddgr",)])
    ctx = build_context(tmp_path)
    request = "search for the arch wiki and open it"
    assert "ddgr --json" in ctx.agent._outgoing(request)[0].content
    data = ctx.store.config.model_dump(mode="json")
    data["shell"]["enabled"] = False
    ctx.store.update(data)
    assert "ddgr --json" not in ctx.agent._outgoing(request)[0].content
