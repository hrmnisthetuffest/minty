"""Focus mode (745), the distraction guard that asks and never blocks (603), scenes (746)."""
import asyncio

import pytest

from tests.plugin_helpers import make_host
from tests.test_pipeline import build, finish, wait_until
from minty_core.focus import DistractionGuard, Focus
from minty_core.overlay.xwin import ActiveWindow
from minty_core.scenes import SceneError, launch, parse_target


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_focus_runs_for_its_time_and_is_remembered_across_a_restart(tmp_path):
    clock = Clock()
    f = Focus(tmp_path / "focus.json", clock=clock)
    assert f.active() is False
    f.start(25, "maths")
    assert f.active() and round(f.remaining()) == 1500 and f.label == "maths"
    assert Focus(tmp_path / "focus.json", clock=clock).active()  # a restart does not end it
    clock.t += 1501
    assert f.active() is False
    f.start(10, "x")
    f.stop()
    assert f.active() is False


def test_only_urgent_notices_pass_while_focused():
    clock = Clock()
    import tempfile
    from pathlib import Path

    f = Focus(Path(tempfile.mkdtemp()) / "focus.json", clock=clock)
    assert f.holds({"text": "mail arrived"}) is False  # not focused: nothing is held
    f.start(25, "maths")
    assert f.holds({"text": "mail arrived"}) is True
    assert f.holds({"text": "Your tea timer is done.", "urgent": True}) is False


async def test_held_notices_come_back_as_one_line_when_focus_ends(tmp_path):
    p, f = build(tmp_path, stt_texts=[], replies=[])
    clock = Clock()
    focus = Focus(tmp_path / "focus.json", clock=clock)
    focus.start(25, "maths")
    p.focus = focus
    task = asyncio.create_task(p.run())
    f.bus.publish("notify", text="Mail from Dad.")
    f.bus.publish("notify", text="Weather update.")
    f.bus.publish("notify", text="Timer done.", urgent=True)
    await wait_until(lambda: f.player.played == [b"Timer done."])
    await asyncio.sleep(0.2)
    assert f.player.played == [b"Timer done."]  # the others are waiting
    clock.t += 1600  # focus is over
    await wait_until(lambda: len(f.player.played) == 2, timeout=4)
    await finish(p, f, task)
    assert f.player.played[1] == b"While you were focused: Mail from Dad. Weather update."


def test_the_guard_asks_once_after_a_distraction_has_gone_on_and_never_blocks():
    clock = Clock()
    import tempfile
    from pathlib import Path

    focus = Focus(Path(tempfile.mkdtemp()) / "focus.json", clock=clock)
    guard = DistractionGuard(focus, ["youtube", "instagram"], clock=clock, grace_s=20, every_s=600)
    yt = ActiveWindow("0x1", "Funny cats - YouTube - Mozilla Firefox", "firefox", 1)
    docs = ActiveWindow("0x2", "notes.pdf", "evince", 2)
    assert guard.check(yt) is None  # not focusing: it says nothing
    focus.start(25, "maths")
    assert guard.check(yt) is None  # just arrived
    clock.t += 25
    q = guard.check(yt)
    assert q is not None and "YouTube" in q and "maths" in q and "end focus" in q.lower()
    assert guard.check(yt) is None  # asked once
    clock.t += 700
    assert guard.check(yt) is not None  # and again after ten minutes, if it is still going on
    clock.t += 5
    assert guard.check(docs) is None and guard.check(None) is None


async def test_the_focus_tools_start_a_timer_hold_notices_and_stop(tmp_path):
    host = make_host(tmp_path)
    out = await host.call("timers_start_focus", {"minutes": 25, "label": "maths"})
    assert "Focus" in out and "25" in out
    assert "maths" in await host.call("timers_list_timers", {})
    assert "Focus ended" in await host.call("timers_stop_focus", {})
    assert "No timers running." == await host.call("timers_list_timers", {})


def test_a_scene_target_is_a_web_address_or_an_app_id_and_nothing_else():
    assert parse_target("https://docs.google.com/document/d/1") == ("url", "https://docs.google.com/document/d/1")
    assert parse_target("app:org.gnome.Calculator") == ("app", "org.gnome.Calculator")
    for bad in ("rm -rf /", "file:///etc/passwd", "javascript:alert(1)", "app:../../bin/sh", "app:x y", "",
                "https://x.y/\nwhoami"):
        with pytest.raises(SceneError):
            parse_target(bad)


def test_launching_uses_xdg_open_and_gtk_launch_with_no_shell():
    calls = []
    scenes = {"study": ["https://example.org/notes", "app:org.gnome.Calculator"]}
    done = launch("study", scenes, run=lambda argv: calls.append(argv) or 0)
    assert calls == [["xdg-open", "https://example.org/notes"], ["gtk-launch", "org.gnome.Calculator"]]
    assert done == 2
    with pytest.raises(SceneError):
        launch("nope", scenes, run=lambda argv: 0)


async def test_the_scene_tool_asks_before_opening_anything(tmp_path):
    host = make_host(tmp_path)
    data = host._store.config.model_dump(mode="json")
    data["overlay"]["scenes"] = {"study": ["https://example.org/notes"]}
    host._store.update(data)
    tool = host.get_tool("scenes_launch")
    assert tool is not None and tool.risk == 2
    assert "example.org" in tool.describe({"name": "study"})
    assert "study" in await host.call("scenes_list", {})


def test_end_focus_by_voice_stops_it_and_its_timer(tmp_path):
    from types import SimpleNamespace

    from minty_core.app import _end_focus
    from minty_core.timerstore import TimerStore

    clock = Clock()
    store, focus = TimerStore(tmp_path / "timers.json", clock=clock), Focus(tmp_path / "focus.json", clock=clock)
    store.add(1500, "maths", kind="focus")
    store.add(300, "tea")
    focus.start(25, "maths")
    ctx = SimpleNamespace(services={"focus": focus, "timers": store})
    assert _end_focus(ctx) == "Focus ended." and focus.active() is False
    assert [t.label for t in store.all()] == ["tea"]
    assert _end_focus(ctx) == "You're not in focus mode."
