"""Smart end-of-turn (501): a sentence that sounds unfinished waits a moment for the rest."""
import asyncio

import pytest

from tests.test_pipeline import build, finish, wait_until
from minty_core.voice.turnhold import unfinished


@pytest.mark.parametrize("text, why", [
    ("remind me to call mom and", "trailing word"), ("play something by", "trailing word"),
    ("so I was thinking um", "filler"), ("set a timer for...", "trailing pause"), ("can you open the", "trailing word"),
    ("what's the weather like in, ", "trailing pause"),
])
def test_unfinished_sentences(text, why):
    assert unfinished(text) == why


@pytest.mark.parametrize("text", ["what time is it", "stop", "and", "play some music", "turn it up", "thanks",
                                  "how do I get to the station", ""])
def test_finished_or_too_short_to_judge(text):
    assert unfinished(text) == ""


def _build(tmp_path, texts, smart=True):
    p, f = build(tmp_path, stt_texts=texts, replies=[["Done."]])
    data = p.store.config.model_dump(mode="json")
    data["audio"]["smart_turn"] = smart
    data["audio"]["turn_hold_ms"] = 1200
    p.store.update(data)
    return p, f


async def test_the_rest_of_the_sentence_is_joined_to_the_first_part(tmp_path):
    p, f = _build(tmp_path, ["remind me to call mom and", "tell dad about the trip"])
    f.audio.push(b"WAKE", b"SPEECH", b"END", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: f.agent.heard)
    await finish(p, f, task)
    assert f.agent.heard == ["remind me to call mom and tell dad about the trip"]
    assert len(f.stt.calls) == 2


async def test_nothing_follows_so_the_first_part_goes_on_after_the_hold(tmp_path):
    p, f = _build(tmp_path, ["remind me to call mom and"])
    f.audio.push(b"WAKE", b"SPEECH", b"END", *([b"quiet"] * 20))
    task = asyncio.create_task(p.run())
    await wait_until(lambda: f.agent.heard)
    await finish(p, f, task)
    assert f.agent.heard == ["remind me to call mom and"] and len(f.stt.calls) == 1


async def test_a_finished_sentence_is_not_held_at_all(tmp_path):
    p, f = _build(tmp_path, ["what time is it"])
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: f.agent.heard)
    await finish(p, f, task)
    assert f.agent.heard == ["what time is it"]


async def test_off_by_default_changes_nothing(tmp_path):
    p, f = _build(tmp_path, ["remind me to call mom and"], smart=False)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: f.agent.heard)
    await finish(p, f, task)
    assert f.agent.heard == ["remind me to call mom and"] and len(f.stt.calls) == 1
