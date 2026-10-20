"""Request diet for browser follow-ups (W0-12): one full page reading, a short browser tool list."""
from tests.test_agent_toolkits import make
from tests.test_toolkits import PLUGINS, offered
from minty_core.llm.types import Message, TextDelta, ToolCall
from minty_core.toolkits import BROWSER_ESSENTIALS, ToolkitRouter
from minty_core.webpage import PAGE_TAG, stub_old_pages

BROWSER = PLUGINS["browser"] + ["browser_type", "browser_press", "browser_scroll", "browser_back", "browser_read",
                                "browser_tabs", "browser_select", "browser_fill_form", "browser_macro_run",
                                "browser_screenshot", "browser_search", "browser_wait"]


def page(title, n=3000):
    return f"{PAGE_TAG}\nPage: {title!r} https://shop.example/{title}\nElements:\n" + "[e1] link \"x\"\n" * (n // 14)


def convo():
    msgs = []
    for i, title in enumerate(("home", "search", "results")):
        msgs += [Message("user", f"step {i}"),
                 Message("assistant", "", tool_calls=[ToolCall(f"c{i}", "browser_look", {})]),
                 Message("tool", page(title), tool_call_id=f"c{i}", name="browser_look"),
                 Message("assistant", "ok")]
    msgs += [Message("user", "what time is it"),
             Message("assistant", "", tool_calls=[ToolCall("t", "clock_get_time", {})]),
             Message("tool", "It is noon. " * 40, tool_call_id="t", name="clock_get_time")]
    return msgs


def test_only_the_latest_page_reading_stays_whole():
    msgs = convo()
    out = stub_old_pages(msgs)
    pages = [m for m in out if m.role == "tool" and m.content.startswith(PAGE_TAG)]
    assert pages[-1].content == page("results")
    assert all(len(p.content) < 160 for p in pages[:-1])
    assert "'home'" in pages[0].content and "newer reading" in pages[0].content
    assert out[-1].content == msgs[-1].content  # other tool results are untouched
    assert len(out) == len(msgs) and msgs[2].content == page("home")  # the history itself is not changed


def test_stubbing_a_conversation_without_pages_changes_nothing():
    msgs = [Message("user", "hi"), Message("assistant", "hello")]
    assert stub_old_pages(msgs) == msgs


def router(**kw):
    plugins = dict(PLUGINS, browser=BROWSER)
    return ToolkitRouter(lambda: plugins, **kw)


def test_follow_ups_with_the_window_open_get_the_essential_browser_tools():
    r = router(live=lambda: {"browser"})
    r.next_turn()
    tools = {t for t in offered(r, "click store") if t.startswith("browser_")}
    assert tools == set(BROWSER_ESSENTIALS) & set(BROWSER) and 7 <= len(tools) <= 9
    assert "browser_macro_run" not in tools


def test_need_toolkit_brings_back_the_whole_browser_toolkit():
    r = router(live=lambda: {"browser"})
    r.next_turn()
    r.need("browser", "pick size M")
    assert "browser_select" in offered(r, "pick size M")


def test_no_window_means_the_whole_toolkit_for_the_first_request():
    r = router(live=lambda: set())
    r.next_turn()
    assert "browser_fill_form" in offered(r, "open amazon and fill in the form")


def test_diet_switched_off_offers_everything_as_before():
    r = router(live=lambda: {"browser"}, diet=lambda: False)
    r.next_turn()
    assert "browser_macro_run" in offered(r, "click store")


async def test_agent_sends_stubbed_history_but_keeps_its_own(tmp_path):
    agent, router_, host = make(tmp_path, [[TextDelta("Done.")]])
    sent = []
    original = router_.stream

    async def recording(messages, tools, **kw):
        sent.append(list(messages))
        async for ev in original(messages, tools, **kw):
            yield ev

    router_.stream = recording
    agent.history = convo()[:-3]
    [_ async for _ in agent.respond("click store")]
    pages = [m for m in sent[0] if m.role == "tool" and m.content.startswith(PAGE_TAG)]
    assert len(pages) == 3 and len(pages[0].content) < 160 and pages[-1].content == page("results")
    assert agent.history[2].content == page("home")


async def test_agent_without_diet_sends_everything(tmp_path):
    agent, router_, host = make(tmp_path, [[TextDelta("Done.")]])
    data = agent._store.config.model_dump(mode="json")
    data["turn"]["request_diet"] = False
    agent._store.update(data)
    sent = []
    original = router_.stream

    async def recording(messages, tools, **kw):
        sent.append(list(messages))
        async for ev in original(messages, tools, **kw):
            yield ev

    router_.stream = recording
    agent.history = convo()[:-3]
    [_ async for _ in agent.respond("click store")]
    assert [m.content for m in sent[0][1:4]] == [m.content for m in convo()[:3]]
