"""Spoken correction (510) and learned vocabulary (509)."""
import asyncio

import pytest

from tests.test_pipeline import build, finish, wait_until
from minty_core.voice.corrections import Vocabulary, drop_last_turn, parse
from minty_core.voice.extras import VoiceExtras


@pytest.mark.parametrize("said, last, want", [
    ("no, I said weather in Paris", "weather in Paris", "weather in Paris"),
    ("I said play Tum Hi Ho", "play tom he who", "play Tum Hi Ho"),
    ("I meant tomorrow", "what's the weather today", "what's the weather tomorrow"),
    ("not Perry, Paris", "weather in Perry", "weather in Paris"),
    ("no it's Paris", "weather in Perry", "weather in Paris"),
    ("that's not what I said, I said call Dad", "call that", "call Dad"),
])
def test_corrections_become_the_request(said, last, want):
    c = parse(said, last)
    assert c is not None and c.text == want


@pytest.mark.parametrize("text", ["what time is it", "no", "I said hello to him yesterday and then left", "not now"])
def test_ordinary_sentences_are_not_corrections(text):
    assert parse(text, "what's the weather in Paris") is None


def test_a_correction_needs_a_previous_request_unless_it_is_a_full_sentence():
    assert parse("not Perry, Paris", "") is None


def test_the_vocabulary_learns_new_words_once_and_stays_small(tmp_path):
    v = Vocabulary(tmp_path / "vocab.json", cap=2)
    assert v.learn("weather in Perry", "weather in Paris") == ["Paris"]
    assert v.learn("weather in Perry", "weather in Paris") == []  # stored once
    assert v.learn("play tom he who", "play Tum Hi Ho") == ["Tum Hi Ho"]
    assert v.learn("open the thing", "open Spotify now") == ["Spotify"]
    assert v.terms() == ["Tum Hi Ho", "Spotify"]  # at most two: the oldest was dropped
    assert "Spotify" in v.hint()
    assert Vocabulary(tmp_path / "vocab.json", cap=2).terms() == v.terms()  # kept on disk


def test_drop_last_turn_removes_the_question_and_its_answer():
    from types import SimpleNamespace

    history = [SimpleNamespace(role="user"), SimpleNamespace(role="assistant"), SimpleNamespace(role="user"),
               SimpleNamespace(role="assistant")]
    assert drop_last_turn(history) == 2 and len(history) == 2
    assert drop_last_turn([]) == 0


async def test_the_pipeline_re_asks_with_the_corrected_words_and_learns_them(tmp_path):
    p, f = build(tmp_path, stt_texts=["weather in Perry", "no I said weather in Paris"],
                 replies=[["Sunny in Perry."], ["Sunny in Paris."]])
    f.agent.history = []
    data = p.store.config.model_dump(mode="json")
    data["voice"]["vocabulary"] = True
    p.store.update(data)
    extras = VoiceExtras(p.store, tmp_path)
    p.voice = extras
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: b"Sunny in Perry." in f.player.played)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: b"Sunny in Paris." in f.player.played)
    await finish(p, f, task)
    assert f.agent.heard == ["weather in Perry", "weather in Paris"]
    assert extras.vocabulary.terms() == ["Paris"]
