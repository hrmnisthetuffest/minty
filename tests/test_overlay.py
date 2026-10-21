"""The overlay's logic: which window is active (X11), when the HUD shows, and what it says (674 676)."""
import asyncio
import os
from pathlib import Path

import pytest

from minty_core.events import EventBus
from minty_core.overlay.feed import OverlayFeed
from minty_core.overlay.model import OverlayModel
from minty_core.overlay.xwin import ActiveWindow, ActiveWindowReader


def xprop(active="0x3a00007", cls='"gnome-terminal-server", "Gnome-terminal"', title='"minty run"', pid="4321"):
    def run(argv):
        assert argv[0] == "xprop"
        if "-root" in argv:
            return 0, f"_NET_ACTIVE_WINDOW(WINDOW): window id # {active}\n"
        return 0, (f"WM_CLASS(STRING) = {cls}\n_NET_WM_NAME(UTF8_STRING) = {title}\n"
                   f"_NET_WM_PID(CARDINAL) = {pid}\n")
    return run


def test_the_active_window_is_read_from_xprop():
    w = ActiveWindowReader(xprop()).read()
    assert w == ActiveWindow("0x3a00007", "minty run", "gnome-terminal-server", 4321)


def test_no_active_window_or_a_failing_xprop_is_none():
    assert ActiveWindowReader(xprop(active="0x0")).read() is None
    assert ActiveWindowReader(lambda argv: (1, "no display")).read() is None
    assert ActiveWindowReader(lambda argv: (127, "xprop is not installed")).available() is False


def model(**kw):
    return OverlayModel(clock=lambda: 100.0, **kw)


def win(cls="firefox", title="YouTube - Mozilla Firefox"):
    return ActiveWindow("0x1", title, cls, 1)


def test_the_hud_is_hidden_when_the_minty_terminal_is_focused_and_shown_for_other_apps():
    m = model()
    terminal = ActiveWindow("0x2", "minty run", "gnome-terminal-server", 4321)
    assert m.frame(terminal).visible is False
    assert m.frame(win()).visible is True
    other_terminal = ActiveWindow("0x3", "vim notes.txt", "gnome-terminal-server", 99)
    assert m.frame(other_terminal).visible is True  # a terminal that is not Minty counts as another window
    assert m.frame(ActiveWindow("0x4", "Minty panel - Chromium", "chromium", 5)).visible is False
    assert m.frame(ActiveWindow("0x5", "Minty HUD", "minty-overlay", 6)).visible is False
    assert m.frame(None).visible is False  # not knowing is not a reason to draw on the screen


def test_it_can_be_switched_off():
    assert model(enabled=False).frame(win()).visible is False


def test_the_frame_carries_state_a_live_caption_and_the_nearest_timers():
    m = model()
    m.apply({"state": "listening", "partial": "what is the", "tentative": " weather", "sentence": "",
             "sentence_age_s": 99, "notice": "", "notice_age_s": 99,
             "timers": [{"label": "tea", "kind": "timer", "remaining_s": 272.4},
                        {"label": "study", "kind": "study", "remaining_s": 1500},
                        {"label": "a", "kind": "timer", "remaining_s": 5}, {"label": "b", "kind": "timer",
                                                                            "remaining_s": 9}]})
    f = m.frame(win())
    assert f.state == "listening" and f.caption == "what is the weather"
    assert f.timers == ["study 25:00", "a 0:05", "b 0:09"]  # a study timer leads, then the nearest; three at most
    m.apply({"state": "speaking", "partial": "", "tentative": "", "sentence": "It is sunny.", "sentence_age_s": 2,
             "timers": [], "notice": "", "notice_age_s": 99})
    assert m.frame(win()).caption == "It is sunny."
    m.apply({"state": "idle", "partial": "", "tentative": "", "sentence": "It is sunny.", "sentence_age_s": 30,
             "timers": [], "notice": "", "notice_age_s": 99})
    assert m.frame(win()).caption == ""  # an old sentence fades


def test_a_notice_shows_for_a_few_seconds():
    m = model()
    m.apply({"state": "idle", "partial": "", "tentative": "", "sentence": "", "sentence_age_s": 99,
             "notice": "Replies are slow.", "notice_age_s": 3, "timers": []})
    assert m.frame(win()).notice == "Replies are slow."
    m.apply({"state": "idle", "partial": "", "tentative": "", "sentence": "", "sentence_age_s": 99,
             "notice": "Replies are slow.", "notice_age_s": 20, "timers": []})
    assert m.frame(win()).notice == ""


async def test_the_feed_follows_the_event_bus():
    bus = EventBus()
    feed = OverlayFeed(bus, clock=lambda: 50.0)
    task = asyncio.create_task(feed.run())
    await asyncio.sleep(0)
    bus.publish("state", state="listening")
    bus.publish("partial", text="turn on", tentative=" the light")
    bus.publish("transcript", role="assistant", text="Done.")
    bus.publish("notice", message="Something to know.")
    for _ in range(50):
        snap = feed.snapshot()
        if snap["sentence"] == "Done." and snap["notice"]:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    snap = feed.snapshot()
    assert snap["state"] == "listening" and snap["partial"] == "turn on" and snap["tentative"] == " the light"
    assert snap["sentence"] == "Done." and snap["notice"] == "Something to know."
    assert snap["sentence_age_s"] == 0.0


def test_the_api_serves_the_overlay_poll(tmp_path):
    from fastapi.testclient import TestClient

    from minty_core.api import create_app
    from minty_core.context import build_context
    from minty_core.timerstore import TimerStore

    ctx = build_context(tmp_path)
    store = TimerStore(tmp_path / "timers.json", clock=lambda: 10.0)
    store.add(120, "tea")
    ctx.services["timers"] = store
    ctx.bus.publish("state", state="listening")
    client = TestClient(create_app(ctx))
    body = client.get("/overlay", headers={"Authorization": f"Bearer {ctx.token}"}).json()
    assert body["timers"] == [{"label": "tea", "kind": "timer", "remaining_s": 120.0}]
    assert {"state", "partial", "sentence", "notice"} <= set(body)


DRAW_SCRIPT = """
import sys
import tkinter as tk
try:
    tk.Tk().destroy()
except tk.TclError:
    sys.exit(3)  # no display
from minty_core.overlay.model import OverlayModel
from minty_core.overlay.window import HudWindow
from minty_core.overlay.xwin import ActiveWindow

snap = {"state": "speaking", "partial": "", "tentative": "", "sentence": "It is sunny.", "sentence_age_s": 1,
        "notice": "", "notice_age_s": 99, "timers": [{"label": "tea", "kind": "timer", "remaining_s": 65}]}
m = OverlayModel(enabled=True)
reader = type("R", (), {"read": lambda self: ActiveWindow("0x1", "Firefox", "firefox", 1)})()
hud = HudWindow(m, reader, lambda: snap, never_map=True)
try:
    frame = hud.update()
    assert frame.visible and hud.caption_label.cget("text") == "It is sunny."
    assert hud.timers_label.cget("text") == "tea 1:05" and hud.root.state() == "withdrawn"
finally:
    hud.root.destroy()
print("OK")
"""


def test_the_window_draws_a_frame_without_ever_being_mapped(tmp_path):
    """Run in its own process: Tk can abort the whole interpreter when the X server misbehaves, and that must
    never take the test run down with it."""
    import subprocess
    import sys

    pytest.importorskip("tkinter")
    src = str(Path(__file__).resolve().parents[1] / "src")
    try:
        done = subprocess.run([sys.executable, "-c", DRAW_SCRIPT], capture_output=True, text=True, timeout=30,
                              env={**os.environ, "PYTHONPATH": src})
    except subprocess.TimeoutExpired:
        pytest.skip("Tk did not answer in time")
    if done.returncode == 3:
        pytest.skip("no display")
    if done.returncode < 0:
        pytest.skip(f"Tk aborted in its own process (signal {-done.returncode}); the display is not usable here")
    assert done.returncode == 0 and "OK" in done.stdout, done.stderr[-400:]


def test_a_running_recording_shows_a_red_marker_first_on_the_hud(tmp_path):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from minty_core.api import create_app
    from minty_core.context import build_context

    ctx = build_context(tmp_path)
    rec = SimpleNamespace(recording=True, elapsed=lambda: 125.0)
    ctx.services["recorder"] = rec
    client = TestClient(create_app(ctx))
    body = client.get("/overlay", headers={"Authorization": f"Bearer {ctx.token}"}).json()
    assert body["timers"][0] == {"label": "● REC", "kind": "recording", "remaining_s": 125.0}
    m = OverlayModel(enabled=True)
    m.apply({**body, "timers": [*body["timers"], {"label": "tea", "kind": "timer", "remaining_s": 10}]})
    frame = m.frame(ActiveWindow("0x2", "Firefox", "firefox", 7))
    assert frame.timers[0] == "● REC 2:05"
