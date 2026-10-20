"""A closed Chrome window no longer poisons the browser session (W0-09)."""
import pytest

from tests.test_plugin_browser import browser_host


class TargetClosedError(Exception):
    """Same name as Playwright's; the plugin recognises it by name."""


class FakePage:
    def __init__(self, world):
        self.world, self.closed, self.url = world, False, "about:blank"

    def is_closed(self):
        return self.closed

    def on(self, *a):
        pass

    async def goto(self, url, **kw):
        if self.world.dead_on_goto:
            self.world.dead_on_goto -= 1
            raise TargetClosedError("Target page, context or browser has been closed")
        self.world.visited.append(url)
        self.url = url

    async def bring_to_front(self):
        self.world.fronted += 1

    async def wait_for_load_state(self, *a, **kw):
        pass

    async def evaluate(self, *a, **kw):
        return "1:1"

    async def title(self):
        return "Fake"

    def locator(self, *a, **kw):
        raise RuntimeError("no page reading in this fake")


class FakeContext:
    def __init__(self, world):
        self.world, self.handlers = world, {}
        self.pages = [FakePage(world)]

    async def route(self, *a):
        pass

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    async def new_page(self):
        page = FakePage(self.world)
        self.pages.append(page)
        return page

    def closed_by_user(self):
        for page in self.pages:
            page.closed = True
        for handler in self.handlers.get("close", []):
            handler(self)

    async def close(self):
        self.closed_by_user()


class World:
    def __init__(self):
        self.launches, self.visited, self.fronted, self.stopped = 0, [], 0, 0
        self.dead_on_goto = 0
        self.contexts: list[FakeContext] = []


@pytest.fixture
def world(monkeypatch):
    w = World()

    class Chromium:
        async def launch_persistent_context(self, path, **kw):
            w.launches += 1
            ctx = FakeContext(w)
            w.contexts.append(ctx)
            return ctx

    class PW:
        chromium = Chromium()

        async def stop(self):
            w.stopped += 1

    class Starter:
        async def start(self):
            return PW()

    import playwright.async_api as api
    monkeypatch.setattr(api, "async_playwright", lambda: Starter())
    return w


async def test_window_closed_by_the_user_relaunches_on_the_next_call(tmp_path, world):
    host = browser_host(tmp_path)
    await host.call("browser_open", {"url": "https://example.com"})
    world.contexts[-1].closed_by_user()
    out = await host.call("browser_open", {"url": "https://example.org"})
    assert world.launches == 2 and world.visited == ["https://example.com", "https://example.org"]
    assert "TargetClosedError" not in out
    assert world.stopped == 1  # the old Playwright driver is not left running


async def test_a_missed_close_event_is_recovered_from_the_error(tmp_path, world):
    host = browser_host(tmp_path)
    await host.call("browser_open", {"url": "https://example.com"})
    world.dead_on_goto = 1  # the window died without telling us
    out = await host.call("browser_open", {"url": "https://example.org"})
    assert world.launches == 2 and world.visited[-1] == "https://example.org"
    assert "TargetClosedError" not in out


async def test_open_with_no_address_on_an_open_session_shows_the_window(tmp_path, world):
    host = browser_host(tmp_path)
    await host.call("browser_open", {"url": "https://example.com"})
    await host.call("browser_open", {})
    assert world.fronted == 1 and world.visited == ["https://example.com"] and world.launches == 1


async def test_open_with_no_address_and_no_session_starts_on_google(tmp_path, world):
    host = browser_host(tmp_path)
    await host.call("browser_open", {})
    assert world.visited == ["https://www.google.com"] and world.fronted == 0
