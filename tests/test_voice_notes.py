"""Noting mode: "note for me" keeps listening, with no wake word, writing down every utterance until "Minty, stop noting";
the latest notes are listed on the HUD."""
import asyncio
from datetime import datetime

import pytest

from minty_core.events import EventBus
from minty_core.overlay.feed import OverlayFeed
from minty_core.overlay.model import OverlayModel
from minty_core.overlay.xwin import ActiveWindow
from minty_core.voice.extras import VoiceExtras
from minty_core.voice.notes import NoteTaker, is_start, is_stop
from tests.test_pipeline import build, finish, wait_until


# -- the phrases ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["note for me", "Note for me.", "Minty, note for me", "hey Minty note for me please",
                                  "start noting", "take notes for me", "noting mode"])
def test_these_start_noting(text):
    assert is_start(text)


@pytest.mark.parametrize("text", ["I need a note for me to read later about milk", "what is a note", "stop noting",
                                  "notes", "", "remind me to note for me"])
def test_a_longer_sentence_or_a_stop_is_not_a_start(text):
    assert not is_start(text)


@pytest.mark.parametrize("text", ["Minty stop noting", "minty, stop noting.", "Hey Minty, stop noting", "stop noting",
                                  "minty stop taking notes"])
def test_these_stop_noting(text):
    assert is_stop(text)


@pytest.mark.parametrize("text", ["we should stop noting every little thing in the meeting", "minty stop", "stop",
                                  "keep noting", "minty stop dictation"])
def test_only_the_whole_phrase_stops_it(text):
    assert not is_stop(text)


# -- the notes ----------------------------------------------------------------------------------------------------------

def taker(tmp_path):
    clock = iter(datetime(2026, 10, 19, 22, 50, s) for s in range(0, 60))
    return NoteTaker(tmp_path / "voice-notes.md", clock=lambda: next(clock))


def test_every_note_is_on_disk_the_moment_it_is_added(tmp_path):
    n = taker(tmp_path)
    n.begin()
    assert n.active and not (tmp_path / "voice-notes.md").exists()  # nothing yet: no empty sessions
    assert n.add("buy milk") == "buy milk"
    text = (tmp_path / "voice-notes.md").read_text()
    assert text.startswith("## 2026-10-19 22:50") and "- 22:50 buy milk" in text
    n.add("call dad at five")
    assert n.recent(5) == ["buy milk", "call dad at five"] and n.recent(1) == ["call dad at five"]
    assert n.end() == 2 and not n.active


def test_filler_and_empty_utterances_are_not_notes(tmp_path):
    n = taker(tmp_path)
    n.begin()
    assert n.add("  ") is None and n.add("um") is None and n.add("Uh.") is None and n.add("hmm") is None
    assert n.end() == 0 and not (tmp_path / "voice-notes.md").exists()


def test_a_second_session_gets_its_own_heading_and_the_first_is_kept(tmp_path):
    n = taker(tmp_path)
    n.begin()
    n.add("one")
    n.end()
    n.begin()
    n.add("two")
    text = (tmp_path / "voice-notes.md").read_text()
    assert text.count("## 2026-10-19") == 2 and "- 22:50 one" in text and "two" in text and n.recent(5) == ["two"]


# -- saying it ----------------------------------------------------------------------------------------------------------

def test_the_extras_start_noting_on_the_phrase_and_it_can_be_switched_off(tmp_path):
    p, _ = build(tmp_path)
    v = VoiceExtras(p.store, tmp_path)
    act = v.intercept("note for me", "")
    assert act is not None and act.start_noting and "writing everything down" in act.reply and "stop noting" not in act.reply.lower()  # the spoken reply must never contain the stop phrase: a microphone that hears it would stop the mode
    data = p.store.config.model_dump(mode="json")
    data["voice"]["notes"] = False
    p.store.update(data)
    assert v.intercept("note for me", "") is None or not v.intercept("note for me", "").start_noting


def _pipeline(tmp_path, texts):
    p, f = build(tmp_path, stt_texts=texts)
    p.voice = VoiceExtras(p.store, tmp_path)
    return p, f


async def test_everything_said_is_noted_until_the_stop_phrase_and_nothing_reaches_the_assistant(tmp_path):
    p, f = _pipeline(tmp_path, ["note for me", "buy milk and eggs", "turn off the lights", "call dad at five",
                                "Minty, stop noting."])
    f.audio.push(b"WAKE", b"SPEECH", b"END", *([b"SPEECH", b"END"] * 4))
    task = asyncio.create_task(p.run())
    await wait_until(lambda: any(b"Stopped noting" in x for x in f.player.played), timeout=4)
    await finish(p, f, task)
    assert p.voice.notes.recent(10) == ["buy milk and eggs", "turn off the lights", "call dad at five"]
    assert f.agent.heard == [] and not p.voice.notes.active
    assert any(b"3 notes" in x for x in f.player.played)
    saved = (tmp_path / "voice-notes.md").read_text()
    assert "buy milk and eggs" in saved and "stop noting" not in saved.lower()


async def test_a_long_silence_does_not_end_it(tmp_path):
    p, f = _pipeline(tmp_path, ["note for me", "first thought", "second thought", "minty stop noting"])
    f.audio.push(b"WAKE", b"SPEECH", b"END", b"SPEECH", b"END", *([b"TIMEOUT"] * 5), b"SPEECH", b"END", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: any(b"Stopped noting" in x for x in f.player.played), timeout=4)
    await finish(p, f, task)
    assert p.voice.notes.recent(10) == ["first thought", "second thought"]


async def test_the_bus_carries_the_notes_to_the_hud(tmp_path):
    p, f = _pipeline(tmp_path, ["note for me", "buy milk", "minty stop noting"])
    events = f.bus.subscribe({"notes"})
    f.audio.push(b"WAKE", b"SPEECH", b"END", b"SPEECH", b"END", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: any(b"Stopped noting" in x for x in f.player.played), timeout=4)
    await finish(p, f, task)
    seen = list(events._queue)
    assert seen[0]["active"] is True and seen[0]["items"] == []
    assert any(e["items"] == ["buy milk"] and e["active"] for e in seen)
    assert seen[-1]["active"] is False


async def test_noting_can_be_turned_off(tmp_path):
    p, f = _pipeline(tmp_path, ["note for me"])
    data = p.store.config.model_dump(mode="json")
    data["voice"]["notes"] = False
    p.store.update(data)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: f.agent.heard)
    await finish(p, f, task)
    assert f.agent.heard == ["note for me"] and not p.voice.notes.active


# -- the HUD ------------------------------------------------------------------------------------------------------------

async def test_the_feed_remembers_the_latest_notes():
    bus = EventBus()
    feed = OverlayFeed(bus, clock=lambda: 5.0)
    task = asyncio.create_task(feed.run())
    await asyncio.sleep(0)
    bus.publish("notes", active=True, items=["buy milk", "call dad"])
    for _ in range(50):
        if feed.snapshot().get("noting"):
            break
        await asyncio.sleep(0.01)
    snap = feed.snapshot()
    assert snap["noting"] is True and snap["notes"] == ["buy milk", "call dad"]
    bus.publish("notes", active=False, items=["buy milk", "call dad"])
    for _ in range(50):
        if not feed.snapshot()["noting"]:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert feed.snapshot()["noting"] is False and feed.snapshot()["notes"] == []  # the list leaves with the mode


def test_the_frame_lists_the_notes_while_noting():
    m = OverlayModel(clock=lambda: 1.0)
    m.apply({"state": "listening", "partial": "", "tentative": "", "sentence": "", "sentence_age_s": 99, "notice": "",
             "notice_age_s": 99, "timers": [], "noting": True, "notes": ["buy milk", "call dad"]})
    f = m.frame(ActiveWindow("0x1", "Firefox", "firefox", 1))
    assert f.noting is True and f.notes == ["buy milk", "call dad"]
    m.apply({"state": "idle", "timers": []})
    assert m.frame(ActiveWindow("0x1", "Firefox", "firefox", 1)).notes == []
