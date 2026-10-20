"""Repeating a request while Minty is still thinking does not restart it (W0-08)."""
import asyncio

import pytest

from tests.test_pipeline import build, finish, wait_until
from tests.test_turn_deadline import DelayedAgent, set_turn
from minty_core.pipeline import STILL_WORKING
from minty_core.text import is_repeat, word_overlap


@pytest.mark.parametrize("a, b, repeat", [
    ("open chrome", "open chrome", True),
    ("Open Chrome", "I said open Chrome", True),
    ("could you open chrome", "open chrome please", True),
    ("search for cats", "Minty, search for cats", True),
    ("click on phones", "no, the other one", False),
    ("click on phones", "click on store", False),
    ("open chrome", "open youtube", False),
    ("open chrome", "no, open chrome", False),  # a correction always cancels
    ("open chrome", "stop", False),
    ("open chrome", "", False),
])
def test_is_repeat(a, b, repeat):
    assert is_repeat(a, b, wake_words=("minty",)) is repeat


def test_word_overlap_ignores_filler_and_wake_words():
    assert word_overlap("open chrome", "I said open chrome", wake_words=()) == 1.0
    assert word_overlap("click on phones", "click on store", wake_words=()) == 0.5


async def test_repeat_while_thinking_keeps_the_request_running(tmp_path):
    p, f = build(tmp_path, stt_texts=["open chrome", "I said open chrome"])
    p.agent = f.agent = DelayedAgent([[(0.6, "Chrome is open.")]])
    set_turn(p, notice_after_s=0)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: p.state == "processing")
    await asyncio.sleep(0.05)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: b"Chrome is open." in f.player.played)
    await finish(p, f, task)
    assert f.agent.heard == ["open chrome"]
    assert f.player.played == [STILL_WORKING.encode(), b"Chrome is open."]


async def test_still_working_is_said_only_once(tmp_path):
    p, f = build(tmp_path, stt_texts=["open chrome", "open chrome", "open chrome"])
    p.agent = f.agent = DelayedAgent([[(0.8, "Chrome is open.")]])
    set_turn(p, notice_after_s=0)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: p.state == "processing")
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: len(f.stt.calls) == 2)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: b"Chrome is open." in f.player.played)
    await finish(p, f, task)
    assert f.agent.heard == ["open chrome"] and f.player.played.count(STILL_WORKING.encode()) == 1


async def test_a_different_request_cancels_and_is_answered_without_asking_again(tmp_path):
    p, f = build(tmp_path, stt_texts=["click on phones", "no, the other one"])
    p.agent = f.agent = DelayedAgent([[(1.0, "Clicked phones.")], [(0.0, "Clicked the other one.")]])
    set_turn(p, notice_after_s=0)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: p.state == "processing")
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: b"Clicked the other one." in f.player.played)
    await finish(p, f, task)
    assert f.agent.heard == ["click on phones", "no, the other one"]
    assert b"Clicked phones." not in f.player.played and len(f.stt.calls) == 2


async def test_wake_while_speaking_still_barges_in(tmp_path):
    p, f = build(tmp_path, stt_texts=["first", "second"], replies=[["One.", "Two."], ["Reply two."]], player_delay=0.4)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: b"One." in f.player.played)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: b"Reply two." in f.player.played)
    await finish(p, f, task)
    assert b"Two." not in f.player.played and f.agent.heard == ["first", "second"]


async def test_old_repeat_outside_the_window_cancels(tmp_path):
    p, f = build(tmp_path, stt_texts=["open chrome", "open chrome"])
    p.agent = f.agent = DelayedAgent([[(0.6, "First.")], [(0.0, "Second.")]])
    set_turn(p, notice_after_s=0, repeat_window_s=0.1)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: p.state == "processing")
    await asyncio.sleep(0.2)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: b"Second." in f.player.played)
    await finish(p, f, task)
    assert f.agent.heard == ["open chrome", "open chrome"]


async def test_switched_off_keeps_the_old_barge_in(tmp_path):
    p, f = build(tmp_path, stt_texts=["open chrome", "open chrome"])
    p.agent = f.agent = DelayedAgent([[(0.6, "First.")], [(0.0, "Second.")]])
    set_turn(p, notice_after_s=0, coalesce_repeats=False)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: p.state == "processing")
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: b"Second." in f.player.played)
    await finish(p, f, task)
    assert f.agent.heard == ["open chrome", "open chrome"] and STILL_WORKING.encode() not in f.player.played


async def test_hearing_my_own_voice_after_the_name_is_ignored(tmp_path):
    from minty_core.pipeline import ONE_MOMENT
    p, f = build(tmp_path, stt_texts=["open chrome", "one moment"])  # the mic caught Minty's own notice
    p.agent = f.agent = DelayedAgent([[(0.5, "Chrome is open.")]])
    set_turn(p, notice_after_s=0.05)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: ONE_MOMENT.encode() in f.player.played)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: b"Chrome is open." in f.player.played)
    await finish(p, f, task)
    assert f.agent.heard == ["open chrome"]  # not cancelled, not answered as a new request
