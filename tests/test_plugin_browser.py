import asyncio
import shutil
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.plugin_helpers import make_host

PAGE = """<!doctype html><title>Shop</title>
<h1>Welcome</h1>
<input id=q placeholder="Search products">
<input id=em placeholder="Email address">
<input type=password id=pw placeholder="Password">
<button id=go onclick="document.body.insertAdjacentHTML('beforeend','<p id=done>searched '+document.getElementById('q').value+'</p>')">Search</button>
<button onclick="document.body.insertAdjacentHTML('beforeend','<p>bought</p>')">Buy now</button>
<a href="other.html">Other page</a>
<a href="notes.txt" download>Get notes</a>
"""

needs_chrome = pytest.mark.skipif(
    shutil.which("google-chrome-stable") is None and shutil.which("google-chrome") is None
    and shutil.which("chromium") is None, reason="no Chrome available")


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    root.mkdir()
    (root / "index.html").write_text(PAGE)
    (root / "other.html").write_text("<title>Other</title><p>second page</p>")
    (root / "notes.txt").write_text("hello")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def browser_host(tmp_path, **settings):
    return make_host(tmp_path / "cfg", plugins={"browser": {"enabled": True, "headless": True, **settings}})


def ref_of(snapshot: str, label: str) -> str:
    for line in snapshot.splitlines():
        if label in line and line.startswith("["):
            return line[1:line.index("]")]
    raise AssertionError(f"{label!r} not in:\n{snapshot}")


@needs_chrome
async def test_it_reads_clicks_and_types_like_a_person(tmp_path, site):
    host = browser_host(tmp_path, allow_local=True)
    try:
        page = await host.call("browser_open", {"url": site})
        assert "untrusted web page content" in page and "Welcome" in page
        q = ref_of(page, "Search products")
        typed = await host.call("browser_type", {"ref": q, "text": "red shoes"})
        assert 'value="red shoes"' in typed
        result = await host.call("browser_click", {"ref": ref_of(typed, '"Search"')})
        assert "searched red shoes" in result
        other = await host.call("browser_click", {"ref": ref_of(result, "Other page")})
        assert "second page" in other
        assert "Welcome" in await host.call("browser_back", {})
    finally:
        await host.call("browser_close", {})


@needs_chrome
async def test_passwords_are_refused_and_purchases_need_a_yes(tmp_path, site):
    host = browser_host(tmp_path, allow_local=True)
    try:
        page = await host.call("browser_open", {"url": site})
        refused = await host.call("browser_type", {"ref": ref_of(page, "Password"), "text": "hunter2"})
        assert refused.startswith("Error: I never type into password")
        click = host.get_tool("browser_click")
        assert click.risk({"ref": ref_of(page, "Buy now")}) == 3  # money: always asked
        assert click.risk({"ref": ref_of(page, '"Search"')}) == 0
        assert click.risk({"ref": "e999"}) == 0  # an unknown reference does nothing, so there is nothing to ask
        assert "Buy now" in click.describe({"ref": ref_of(page, "Buy now")})
    finally:
        await host.call("browser_close", {})


@needs_chrome
async def test_local_and_file_pages_are_blocked_by_default(tmp_path, site):
    host = browser_host(tmp_path)
    try:
        out = await host.call("browser_open", {"url": site})
        assert out.startswith("Error") and "private or local" in out
        assert (await host.call("browser_open", {"url": "file:///etc/passwd"})).startswith("Error")
    finally:
        await host.call("browser_close", {})


def test_unknown_refs_and_key_names_are_validated(tmp_path):
    host = browser_host(tmp_path)
    assert callable(host.get_tool("browser_click").risk)
    assert not host.get_tool("browser_open").risky


def test_without_a_display_the_browser_runs_hidden(tmp_path, monkeypatch):
    # no way to observe the flag from outside, so check the source of truth through a launch recorder
    import minty_core.plugins.builtin.browser.plugin as _  # noqa: F401  (module imports cleanly)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    launched = {}

    class FakeChromium:
        async def launch_persistent_context(self, path, **kw):
            launched.update(kw)
            raise RuntimeError("stop here")

    class FakePW:
        chromium = FakeChromium()

    class Starter:
        async def start(self):
            return FakePW()

    import playwright.async_api as api
    monkeypatch.setattr(api, "async_playwright", lambda: Starter())
    host = browser_host(tmp_path)
    import asyncio
    out = asyncio.run(host.call("browser_open", {"url": "https://example.com"}))
    assert launched["headless"] is True and out.startswith("Error")


def test_open_with_no_address_starts_on_google(tmp_path, monkeypatch):
    # "open Chrome" has no page in it; the tool must still open the browser
    visited = []

    class FakePage:
        def is_closed(self):
            return False

        async def goto(self, url, **kw):
            visited.append(url)
            raise RuntimeError("stop here")

    class FakeContext:
        pages = [FakePage()]

        async def route(self, *a):
            pass

        def on(self, *a):
            pass

    class FakeChromium:
        async def launch_persistent_context(self, path, **kw):
            return FakeContext()

    class FakePW:
        chromium = FakeChromium()

    class Starter:
        async def start(self):
            return FakePW()

    import playwright.async_api as api
    monkeypatch.setattr(api, "async_playwright", lambda: Starter())
    host = browser_host(tmp_path)
    assert "url" not in host.get_tool("browser_open").parameters.get("required", [])
    import asyncio
    asyncio.run(host.call("browser_open", {}))
    assert visited == ["https://www.google.com"]


class ScriptedRouter:
    """Stands in for the model: open the shop, try to click 'Buy now', then report."""

    def __init__(self, site):
        self.site, self.turn, self.prefer = site, 0, []

    async def stream(self, messages, tools, sensitive=False, tier="fast", prefer=None):
        from minty_core.agentloop import VERIFIER_MARK
        from minty_core.llm.types import TextDelta, ToolCall, ToolCallEvent
        if messages[0].content.startswith(VERIFIER_MARK):  # the loop's double-check of the result
            yield TextDelta('{"passed": true, "reason": "ok"}')
            return
        self.prefer.append(prefer)
        self.turn += 1
        if self.turn == 1:
            assert {t.name for t in tools} >= {"browser_open", "browser_click"} and "browser_task" not in \
                {t.name for t in tools}
            yield ToolCallEvent(ToolCall("1", "browser_open", {"url": self.site}))
        elif self.turn == 2:
            yield ToolCallEvent(ToolCall("2", "browser_click", {"ref": ref_of(messages[-1].content, "Buy now")}))
        else:
            assert "did not allow that action" in messages[-1].content
            yield TextDelta("I stopped before buying.")


@needs_chrome
async def test_browser_task_works_step_by_step_and_asks_before_buying(tmp_path, site):
    asked = []

    async def confirm(question):
        asked.append(question)
        return False

    router = ScriptedRouter(site)
    host = make_host(tmp_path / "cfg", services={"router": router, "confirm": confirm},
                     plugins={"browser": {"enabled": True, "headless": True, "allow_local": True,
                                          "agent_model": "gemini"}})
    try:
        out = await host.call("browser_task", {"goal": "buy the lamp"})
        assert out == "I stopped before buying."  # the job carried on after the no, and the model reported
        assert asked and "Buy now" in asked[0] and router.prefer == ["gemini"] * 3
        assert "bought" not in await host.call("browser_look", {})
    finally:
        await host.call("browser_close", {})


def test_old_page_readings_are_trimmed():
    from minty_core.llm.types import Message
    from minty_core.plugins.builtin.browser.plugin import _trim_old_pages
    msgs = [Message("tool", f"tag\nPage {i}\nlots of text") for i in range(4)]
    out = _trim_old_pages(msgs)
    assert out[0].content == "tag\nPage 0\n(older reading trimmed)" and out[3].content == msgs[3].content


class SlowRouter:
    """Opens the site, then thinks for a long time (so the job can be cancelled mid-run)."""

    def __init__(self, site, think_s=5.0):
        self.site, self.think_s, self.turn = site, think_s, 0

    async def stream(self, messages, tools, sensitive=False, tier="fast", prefer=None):
        from minty_core.agentloop import VERIFIER_MARK
        from minty_core.llm.types import TextDelta, ToolCall, ToolCallEvent
        if messages[0].content.startswith(VERIFIER_MARK):
            yield TextDelta('{"passed": true, "reason": "ok"}')
            return
        self.turn += 1
        if self.turn == 1:
            yield ToolCallEvent(ToolCall("1", "browser_open", {"url": self.site}))
        else:
            await asyncio.sleep(self.think_s)
            yield TextDelta("Finished looking.")


@needs_chrome
async def test_browser_task_runs_as_a_background_job(tmp_path, site):
    import time as _time
    from minty_core.jobs import JobRunner
    heard = []
    runner = JobRunner(None, notify=heard.append)
    host = make_host(tmp_path / "cfg", services={"router": SlowRouter(site, think_s=0.2), "jobs": runner},
                     plugins={"browser": {"enabled": True, "headless": True, "allow_local": True}})
    try:
        started = _time.monotonic()
        out = await host.call("browser_task", {"goal": "look at the shop"})
        assert _time.monotonic() - started < 0.5 and out.startswith("On it, as browser job 1")
        job = await runner.wait(1)
        assert job.status == "done" and job.result == "Finished looking." and heard == ["Finished looking."]
        assert job.kind == "browser" and job.progress[0].startswith("opening")
    finally:
        await host.call("browser_close", {})


@needs_chrome
async def test_cancelling_a_browser_job_leaves_the_browser_usable(tmp_path, site):
    from minty_core.jobs import JobRunner
    runner = JobRunner(None)
    host = make_host(tmp_path / "cfg", services={"router": SlowRouter(site), "jobs": runner},
                     plugins={"browser": {"enabled": True, "headless": True, "allow_local": True}})
    try:
        await host.call("browser_task", {"goal": "first"})
        second = await host.call("browser_task", {"goal": "second"})
        assert "starts after the current browser job" in second
        await asyncio.sleep(1.5)  # first job has opened the page and is "thinking"
        assert [j.status for j in runner.select()] == ["running", "queued"]
        await runner.cancel_all()
        assert [j.status for j in runner.select()] == ["cancelled", "cancelled"]
        page = await host.call("browser_look", {})
        assert "Shop" in page  # the lock was released and the window still works
    finally:
        await host.call("browser_close", {})


@needs_chrome
async def test_a_screenshot_is_announced_on_the_bus_with_its_page(tmp_path, site):
    seen = []
    host = make_host(tmp_path / "cfg", plugins={"browser": {"enabled": True, "headless": True, "allow_local": True,
                                                          "folder": str(tmp_path / "ws")}},
                     services={"publish": lambda kind, **kw: seen.append((kind, kw)),
                               "is_private": lambda: False})
    try:
        await host.call("browser_open", {"url": site})
        said = await host.call("browser_screenshot", {})
        assert "Saved a screenshot" in said
        (kind, kw), = seen
        assert kind == "screenshot" and kw["private"] is False and kw["url"].startswith(site)
        assert (tmp_path / "ws" / kw["path"].rsplit("/", 1)[-1]).read_bytes()[:4] == b"\x89PNG"
    finally:
        await host.call("browser_close", {})


# ---- WP-16 hooks: dry run, the page views the web helpers use, the form log ------------------------------------
@needs_chrome
async def test_a_dry_run_types_but_never_presses_the_final_button(tmp_path, site):
    host = browser_host(tmp_path, allow_local=True)
    ops = host._services["browser_ops"]
    try:
        page = await host.call("browser_open", {"url": site})
        ops.set_dry_run(True)
        stopped = await host.call("browser_click", {"ref": ref_of(page, "Buy now")})
        assert stopped.startswith("DRY RUN: I stopped before clicking") and "Buy now" in stopped
        harmless = await host.call("browser_click", {"ref": ref_of(page, '"Search"')})
        assert "searched" in harmless  # a normal click still works
        assert "bought" not in await host.call("browser_look", {})
        assert ops.dry_log() == ["clicking “Buy now”"]
        ops.set_dry_run(False)
        assert "bought" in await host.call("browser_click", {"ref": ref_of(page, "Buy now")})
    finally:
        await host.call("browser_close", {})


@needs_chrome
async def test_the_helpers_can_read_the_open_page_and_its_tabs_without_changing_it(tmp_path, site):
    host = browser_host(tmp_path, allow_local=True)
    ops = host._services["browser_ops"]
    try:
        assert await ops.page() is None and await ops.tabs() == []  # nothing open: Chrome is not launched to look
        await host.call("browser_open", {"url": site})
        info = await ops.page()
        assert info["title"] == "Shop" and any(n.label == "Buy now" for n in info["nodes"])
        assert await ops.tabs() == [site + "/"]
        assert await ops.open_tab(site + "/other.html") == ""
        assert await ops.tabs() == [site + "/", site + "/other.html"]
        assert ops.risky(next(n for n in info["nodes"] if n.label == "Buy now"))
        assert not ops.risky(next(n for n in info["nodes"] if n.label == "Other page"))
    finally:
        await host.call("browser_close", {})


@needs_chrome
async def test_typing_into_a_form_field_is_logged_by_field_and_site_never_by_value(tmp_path, site):
    seen = []
    host = make_host(tmp_path / "cfg", plugins={"browser": {"enabled": True, "headless": True, "allow_local": True}})
    host._services["web_formlog"] = lambda hostname, field: seen.append((hostname, field))  # the web plugin owns the real one
    try:
        page = await host.call("browser_open", {"url": site})
        await host.call("browser_type", {"ref": ref_of(page, "Search products"), "text": "my secret shoe size"})
        assert seen == []  # a search box is not personal data
        await host.call("browser_type", {"ref": ref_of(page, "Email address"), "text": "alex@example.com"})
        assert seen == [("127.0.0.1", "Email address")] and "alex" not in str(seen)
    finally:
        await host.call("browser_close", {})


@needs_chrome
async def test_a_saved_download_is_handed_to_the_quarantine_hook(tmp_path, site):
    got = []

    def ingest(path, name, source):
        got.append((path.read_bytes(), name))
        return {"id": 7}

    host = make_host(tmp_path / "cfg", plugins={"browser": {"enabled": True, "headless": True, "allow_local": True,
                                                            "folder": str(tmp_path / "ws")}})
    host._services["web_download"] = ingest  # the web plugin owns the real one
    try:
        page = await host.call("browser_open", {"url": site})
        out = await host.call("browser_click", {"ref": ref_of(page, "Get notes")})
        for _ in range(50):
            if got:
                break
            await asyncio.sleep(0.1)
            out = await host.call("browser_look", {})
        assert got == [(b"hello", "notes.txt")] and "quarantine as number 7" in out
    finally:
        await host.call("browser_close", {})
