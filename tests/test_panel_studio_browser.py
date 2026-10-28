"""The studio page in a real (headless, visible) Chrome against a real core: frames advance, a switch uses the clip loaded ahead, the
backdrop plays dimmed, and leaving the page lets every video go. Skipped where Playwright or Chrome is not installed."""
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime
from types import SimpleNamespace

import pytest
import uvicorn

pw = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.skipif(not (shutil.which("google-chrome-stable") or shutil.which("google-chrome")) or not shutil.which("ffmpeg"),
                                reason="Chrome or ffmpeg is not installed")

from minty_core.api import create_app
from minty_core.context import build_context
from minty_core.studio import catalog as cat
from minty_core.studio.assets import AssetLibrary
from minty_panel.auth import LoginThrottle, PasswordStore, SessionStore
from minty_panel.core_client import CoreClient
from minty_panel.server import create_panel_app


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Quiet(uvicorn.Server):
    def install_signal_handlers(self):  # not the main thread
        pass


@pytest.fixture
def running(tmp_path):
    ctx = build_context(tmp_path / "cfg")
    lib = AssetLibrary(tmp_path / "cfg" / "studio" / "library")
    catalog = cat.Catalog(lib, min_clips=1)
    for category, hue in (("nature", 0), ("city", 90)):
        for i in range(2):
            p = tmp_path / f"{category}{i}.mp4"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=30:duration=12", "-vf", f"hue=h={hue + i * 30}",
                            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(p)], check=True)
            catalog.tag(lib.add(p, "video", "cc0", source="test"), category, provider="test", external_id=p.stem)
    ctx.services["studio_player"] = SimpleNamespace(catalog=catalog, lib=lib, thumbs=tmp_path / "cfg" / "studio" / "thumbs", prefer=lambda a: "done", now=datetime.now)
    passwords = PasswordStore(tmp_path)
    passwords.set_pin("493017")
    core_port, panel_port = free_port(), free_port()
    panel = create_panel_app(passwords, SessionStore(), LoginThrottle(), CoreClient(f"http://127.0.0.1:{core_port}", tmp_path / "cfg" / "api_token"),
                             allowed_hosts=(f"127.0.0.1:{panel_port}",))
    servers = [Quiet(uvicorn.Config(create_app(ctx), host="127.0.0.1", port=core_port, log_level="warning", lifespan="off")),
               Quiet(uvicorn.Config(panel, host="127.0.0.1", port=panel_port, log_level="warning"))]
    threads = [threading.Thread(target=s.run, daemon=True) for s in servers]
    for t in threads:
        t.start()
    for _ in range(100):
        if all(s.started for s in servers):
            break
        time.sleep(0.1)
    yield f"http://127.0.0.1:{panel_port}"
    for s in servers:
        s.should_exit = True
    for t in threads:
        t.join(5)


def test_the_studio_page_plays_switches_without_a_gap_and_lets_go(running):
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True, args=["--autoplay-policy=no-user-gesture-required"])
        page = browser.new_page()
        page.goto(running + "/")
        page.evaluate("fetch('/login', {method:'POST', headers:{'Content-Type':'application/json','X-Requested-With':'minty'}, body: JSON.stringify({password:'493017'})})")
        page.goto(running + "/#studio")
        page.reload()
        page.wait_for_selector("video.clip.on[src]", timeout=15000)

        def front():
            return page.evaluate("""() => { const v = document.querySelector('video.clip.on'); return {src: v.getAttribute('src'), paused: v.paused, t: v.currentTime, ready: v.readyState}; }""")

        time.sleep(1.5)
        a = front()
        time.sleep(1.2)
        b = front()
        assert not page.evaluate("document.hidden") and b["t"] > a["t"] and not b["paused"], (a, b)  # frames really advance
        page.get_by_text("Another clip").click()
        time.sleep(1.2)
        assert "loaded ahead" in page.inner_text(".clip-note")
        assert page.evaluate("[...document.querySelectorAll('video.clip')].filter(v => v.getAttribute('src')).length") == 1  # the old one let go
        page.locator("main label.card input[type=checkbox]").last.check()
        time.sleep(2)
        back = page.evaluate("""() => { const v = document.querySelector('video.backdrop'); return v && {paused: v.paused, t: v.currentTime, opacity: getComputedStyle(v).opacity}; }""")
        assert back and not back["paused"] and back["t"] > 0 and float(back["opacity"]) < 0.2
        page.evaluate("location.hash = '#dashboard'")
        time.sleep(0.8)
        assert page.evaluate("document.querySelectorAll('video.clip').length") == 0
        browser.close()
