from types import SimpleNamespace as NS

from tests.plugin_helpers import make_host  # noqa: F401
from minty_core.context import build_context
from minty_core.statusline import snapshot


def test_snapshot_reads_modes_helpers_and_providers(tmp_path):
    ctx = build_context(tmp_path)
    ctx.agent.tier = "deep"
    ctx.agent.force_private = True
    ctx.host._state.setdefault("subagents", {})["jobs"] = {
        1: {"id": 1, "task": "research", "status": "running", "started": 0},
        2: {"id": 2, "task": "old", "status": "done", "started": 0}}
    ctx.host._state.setdefault("agents", {})["jobs"] = {
        5: NS(id=5, agent="claude_code", prompt="fix tests", status="running", started=0.0)}
    st = snapshot(ctx)
    assert st["tier"] == "deep" and st["private"] and st["voice_mode"] == "live"
    assert [j["id"] for j in st["subagents"]] == [1] and [j["id"] for j in st["jobs"]] == [5]
    assert st["brain_total"] >= 0 and st["context_msgs"] == 0 and st["music"] is False


async def test_snapshot_lists_background_jobs_from_the_runner(tmp_path):
    import asyncio
    ctx = build_context(tmp_path)

    async def slow(job_ctx):
        job_ctx.progress("opening the shop")
        await asyncio.sleep(5)

    job = ctx.services["jobs"].submit("browser", "find a lamp", slow)
    await asyncio.sleep(0.05)
    st = snapshot(ctx)
    [w] = st["work"]
    assert w["kind"] == "browser" and w["what"] == "find a lamp" and w["progress"] == "opening the shop"
    await ctx.services["jobs"].cancel(job.id)
    assert snapshot(ctx)["work"] == []


def test_snapshot_carries_the_current_track_for_the_music_card(tmp_path):
    ctx = build_context(tmp_path)
    track = {"title": "Veridis Quo", "artist": "Daft Punk", "album": "", "status": "Playing", "player": "spotify",
             "length_s": 345, "position_s": 60.0}
    ctx.pipeline = NS(music=NS(is_playing=lambda: True, now_playing=lambda: track), muted=False)
    st = snapshot(ctx)
    assert st["now_playing"] == track and st["music"] is True
    ctx.pipeline = NS(music=NS(is_playing=lambda: False, now_playing=lambda: (_ for _ in ()).throw(OSError)), muted=False)
    assert snapshot(ctx)["now_playing"] is None  # a failing player never breaks the display


def test_a_playing_track_always_means_music_even_when_the_flag_is_one_refresh_behind(tmp_path):
    ctx = build_context(tmp_path)
    track = {"title": "A", "artist": "B", "album": "", "status": "Playing", "player": "firefox", "art_url": "",
             "length_s": 100, "position_s": 5.0}
    ctx.pipeline = NS(music=NS(is_playing=lambda: False, now_playing=lambda: track), muted=False)
    assert snapshot(ctx)["music"] is True
    paused = {**track, "status": "Paused"}
    ctx.pipeline = NS(music=NS(is_playing=lambda: False, now_playing=lambda: paused), muted=False)
    assert snapshot(ctx)["music"] is False and snapshot(ctx)["now_playing"] == paused


def test_the_snapshot_lists_what_is_coming_up_soonest_first():
    import time as _time
    from datetime import datetime, timedelta

    from minty_core.scheduler import Item
    from minty_core.timerstore import Timer

    now = datetime.now()  # the timer below runs from the real clock, so the reminders must too
    due = lambda m: now + timedelta(minutes=m)  # noqa: E731
    waiting = [Item(1, "reminder", due(90), "call dad", True, None, "pending"),
               Item(2, "nudge", due(5), "stretch", False, None, "pending"),  # Minty's own nudge: not yours
               Item(3, "reminder", due(1500), "dentist", True, None, "held")]
    timers = [Timer(7, "tea", _time.time() + 272, kind="timer")]
    from minty_core.statusline import coming_up
    got = coming_up(NS(waiting=lambda: waiting), NS(all=lambda: timers), limit=6)
    assert [g["text"] for g in got] == ["tea", "call dad", "dentist"]
    assert [g["kind"] for g in got] == ["timer", "reminder", "reminder"]
    assert got[0]["remaining_s"] > 200 and got[1]["due"] == waiting[0].due.timestamp()
    assert len(coming_up(NS(waiting=lambda: waiting * 5), None, limit=4)) == 4
    assert coming_up(None, None) == []
    boom = NS(waiting=lambda: (_ for _ in ()).throw(RuntimeError("db closed")))
    assert coming_up(boom, None) == []  # a broken store never breaks the display


def test_the_snapshot_carries_the_agenda(tmp_path):
    ctx = build_context(tmp_path)
    assert snapshot(ctx)["agenda"] == []
