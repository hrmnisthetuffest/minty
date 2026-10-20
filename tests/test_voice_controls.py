"""Playback controls and resuming the unspoken tail (513 514), dictation (517), read-aloud (516 567), verbosity and
persona by voice (512 520), settings phrases."""
import asyncio

import pytest

from tests.test_pipeline import build, finish, wait_until
from minty_core.voice.extras import VoiceExtras


def _pipeline(tmp_path, texts, replies, **kw):
    p, f = build(tmp_path, stt_texts=texts, replies=replies, **kw)
    f.agent.history = []
    p.voice = VoiceExtras(p.store, tmp_path)
    saved = []

    async def save(text):
        saved.append(text)
        return "Saved to your journal."

    p.voice.save_note = save
    p.voice.saved = saved
    return p, f


async def say_turn(f, p, text_wait):
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(text_wait)


async def test_go_on_speaks_what_an_interruption_left_unsaid(tmp_path):
    p, f = _pipeline(tmp_path, ["tell me a story", "go on"],
                     [["Once upon a time.", "There was a lamp.", "A genie lived in it.", "The end."]], player_delay=0.25)
    task = asyncio.create_task(p.run())
    await say_turn(f, p, lambda: b"Once upon a time." in f.player.played)
    f.audio.push(b"WAKE", b"SPEECH", b"END")  # the interruption: a new wake word and "go on"
    await wait_until(lambda: any(b"genie" in x for x in f.player.played))
    await finish(p, f, task)
    spoken_after = [x for x in f.player.played if b"genie" in x or b"lamp" in x]
    assert spoken_after and b"The end." in b" ".join(f.player.played)
    assert f.agent.heard == ["tell me a story"]  # "go on" never reached the model


async def test_repeat_that_says_the_last_answer_again(tmp_path):
    p, f = _pipeline(tmp_path, ["what time is it", "repeat that"], [["It is noon.", "Lunch time."]])
    task = asyncio.create_task(p.run())
    await say_turn(f, p, lambda: len(f.player.played) == 2)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: len(f.player.played) == 3)
    await finish(p, f, task)
    assert f.player.played[2] == b"It is noon. Lunch time." and f.agent.heard == ["what time is it"]


async def test_shorter_asks_for_a_shorter_version_of_the_same(tmp_path):
    p, f = _pipeline(tmp_path, ["explain tides", "shorter"], [["Long explanation."], ["Moon pulls water."]])
    task = asyncio.create_task(p.run())
    await say_turn(f, p, lambda: b"Long explanation." in f.player.played)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: b"Moon pulls water." in f.player.played)
    await finish(p, f, task)
    assert len(f.agent.heard) == 2 and "short" in f.agent.heard[1].lower() and "one or two sentences" in f.agent.heard[1]


async def test_dictation_writes_everything_down_and_never_runs_a_command_phrase(tmp_path):
    p, f = _pipeline(tmp_path, ["start dictation", "buy milk and eggs", "turn off the lights", "stop listening",
                                "stop dictation"], [], follow_up=True)
    data = p.store.config.model_dump(mode="json")
    data["audio"]["follow_up"] = True
    p.store.update(data)
    task = asyncio.create_task(p.run())
    f.audio.push(b"WAKE", b"SPEECH", b"END", *([b"x"] * 12), b"SPEECH", b"END", *([b"x"] * 12), b"SPEECH", b"END",
                 *([b"x"] * 12), b"SPEECH", b"END", *([b"x"] * 12), b"SPEECH", b"END")
    await wait_until(lambda: p.voice.saved, timeout=4)
    await finish(p, f, task)
    assert p.voice.saved == ["buy milk and eggs turn off the lights stop listening"]
    assert f.agent.heard == []  # nothing in it reached the assistant
    assert p.voice.dictating is False


@pytest.mark.parametrize("phrase, key, expect", [
    ("be brief", "verbosity", 1), ("more detail please", "verbosity", 7), ("be more formal", "formality", 7),
    ("be less formal", "formality", 3), ("make it funnier", "humor", 6), ("less humour", "humor", 1),
])
def test_verbosity_and_persona_by_voice(tmp_path, phrase, key, expect):
    p, f = _pipeline(tmp_path, [], [])
    act = p.voice.intercept(phrase, "")
    assert act is not None and act.reply
    assert getattr(p.store.config.personality, key) == expect


def test_settings_phrases(tmp_path):
    p, f = _pipeline(tmp_path, [], [])
    assert "whisper" in p.voice.intercept("whisper mode on", "").reply.lower()
    assert p.store.config.voice.whisper is True
    p.voice.intercept("stop whispering", "")
    assert p.store.config.voice.whisper is False
    before = p.store.config.tts.speed
    p.voice.intercept("speak slower", "")
    assert p.store.config.tts.speed < before
    assert p.voice.intercept("say Paris as Pa-ree", "").reply
    assert p.voice.pronunciations.entries() == {"paris": "Pa-ree"}
    assert p.voice.intercept("what time is it", "") is None


def test_the_controls_can_be_switched_off(tmp_path):
    p, f = _pipeline(tmp_path, [], [])
    data = p.store.config.model_dump(mode="json")
    data["voice"].update(playback_controls=False, dictation=False)
    p.store.update(data)
    assert p.voice.intercept("go on", "") is None and p.voice.intercept("start dictation", "") is None


async def test_skip_drops_the_interrupted_sentence_and_goes_on_with_the_rest(tmp_path):
    p, f = _pipeline(tmp_path, ["tell me a story", "skip"],
                     [["Once upon a time.", "There was a lamp.", "A genie lived in it.", "The end."]], player_delay=0.25)
    task = asyncio.create_task(p.run())
    await say_turn(f, p, lambda: b"Once upon a time." in f.player.played)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: any(b"genie" in x for x in f.player.played))
    await finish(p, f, task)
    heard_after = b" ".join(f.player.played[1:])
    assert b"genie" in heard_after and b"The end." in heard_after
    assert f.player.played.count(b"Once upon a time.") == 1  # the skipped sentence was not said again
