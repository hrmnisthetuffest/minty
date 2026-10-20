"""677: the panel in a real phone-sized Chrome: nothing wider than the screen, tap targets big enough, the timeline readable."""
import shutil
import socket
import threading
import time

import httpx
import pytest

from minty_panel.auth import LoginThrottle, PasswordStore, SessionStore
from minty_panel.core_client import CoreClient
from minty_panel.server import create_panel_app

pytestmark = pytest.mark.skipif(shutil.which("google-chrome-stable") is None and shutil.which("google-chrome") is None,
                                reason="no Chrome available")

ROWS = [{"id": "job:2", "ts": 1_760_000_300.0, "kind": "job", "title": "research: sparse attention papers", "status": "running",
         "detail": "reading the third source"},
        {"id": "receipt:5", "ts": 1_760_000_200.0, "kind": "action", "title": "Saved notes.txt to the workspace folder",
         "status": "ok", "detail": "risk 1"},
        {"id": "receipt:4", "ts": 1_759_900_000.0, "kind": "action", "title": "Opened a page", "status": "refused", "detail": "risk 2"}]


@pytest.fixture
def server(tmp_path):
    import uvicorn

    passwords = PasswordStore(tmp_path)
    passwords.set("correct horse")
    token = tmp_path / "api_token"
    token.write_text("T\n")

    def core(req):
        if req.url.path == "/timeline":
            return httpx.Response(200, json=ROWS)
        if req.url.path == "/jobs":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"state": "idle", "muted": False})

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    client = CoreClient("http://core.test", token, httpx.AsyncClient(transport=httpx.MockTransport(core)))
    app = create_panel_app(passwords, SessionStore(), LoginThrottle(), client, allowed_hosts=(f"127.0.0.1:{port}",), lan_only=True)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=5)


async def test_the_login_and_timeline_fit_a_phone_and_have_big_enough_buttons(server):
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="chrome", headless=True)
        try:
            ctx = await browser.new_context(viewport={"width": 375, "height": 812}, has_touch=True, is_mobile=True)
            page = await ctx.new_page()
            await page.goto(server)
            await page.wait_for_selector("input[type=password]")
            assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            await page.fill("input[type=password]", "correct horse")
            await page.click("button")
            await page.wait_for_selector("nav a")
            await page.goto(server + "/#timeline")
            await page.wait_for_selector(".tl")
            text = await page.inner_text("main")
            assert "research: sparse attention papers" in text and "Saved notes.txt" in text and "refused" in text
            assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            heights = await page.evaluate("[...document.querySelectorAll('button, nav a')].map(e => e.getBoundingClientRect().height)")
            assert heights and min(heights) >= 43.5, heights
            nav = await page.evaluate("(() => { const n = document.querySelector('nav'); return [n.scrollWidth > n.clientWidth, "
                                      "getComputedStyle(n).overflowX]; })()")
            assert nav[1] in ("auto", "scroll")  # the menu scrolls sideways instead of wrapping into a tall block
            days = await page.evaluate("document.querySelectorAll('main h3').length")
            assert days == 2  # two different days in the sample
        finally:
            await browser.close()
