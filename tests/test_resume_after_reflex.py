"""Say a quick command (volume, mute, pause) over an answer and Minty does it, then carries on with the answer."""
import asyncio

import pytest

from minty_core.instant.actions import is_reflex
from tests.test_pipeline import finish, wait_until
from tests.test_voice_controls import _pipeline, say_turn

STORY = ["Once upon a time.", "There was a lamp.", "A genie lived in it.", "The end."]


@pytest.mark.parametrize("tool,args,expected", [
    ("system_control", {"action": "volume", "value": "down"}, True), ("system_control", {"action": "mute"}, True),
    ("system_control", {"action": "unmute"}, True), ("spotify_pause", {}, True), ("spotify_resume", {}, True),
    ("spotify_next", {}, True), ("spotify_previous", {}, True),
    ("system_control", {"action": "lock"}, False), ("system_control", {"action": "brightness", "value": "20"}, False),
    ("timers_set_timer", {"seconds": 60}, False), ("reminders_set", {"text": "x"}, False), ("files_delete", {}, False),
    ("system_control", {}, False), ("", {}, False),
])
def test_only_quick_reversible_commands_count(tool, args, expected):
    assert is_reflex(tool, args) is expected


def reflex_agent(f, tool="system_control", args=None):
    """The fake agent reports, as the real one does, which quick command a request just ran."""
    args = args or {"action": "volume", "value": "down"}
    original, f.agent.last_fast = f.agent.respond, None

    async def respond(text, *, sensitive=False):
        f.agent.last_fast = (tool, args) if text == "volume down" else None
        async for sentence in original(text, sensitive=sensitive):
            yield sentence

    f.agent.respond = respond


def interrupted_story(tmp_path, texts, replies, resume=True):
    p, f = _pipeline(tmp_path, texts, replies, player_delay=0.25)
    data = p.store.config.model_dump(mode="json")
    data["voice"]["resume_after_reflex"] = resume
    p.store.update(data)
    reflex_agent(f)
    return p, f


async def interrupt(p, f):
    task = asyncio.create_task(p.run())
    await say_turn(f, p, lambda: b"Once upon a time." in f.player.played)
    f.audio.push(b"WAKE", b"SPEECH", b"END")  # the interruption: a quick command said over the story
    return task


async def test_a_quick_command_runs_and_the_answer_carries_on(tmp_path):
    p, f = interrupted_story(tmp_path, ["tell me a story", "volume down"], [STORY, ["Volume down."]])
    task = await interrupt(p, f)
    await wait_until(lambda: any(b"The end." in x for x in f.player.played))
    await finish(p, f, task)
    played = f.player.played
    done = played.index(b"Volume down.")
    rest = next(i for i, x in enumerate(played) if b"genie" in x and b"The end." in x)
    assert done < rest  # the command first, then the rest of the story (the cut-off sentence is said again)
    assert f.agent.heard == ["tell me a story", "volume down"]  # the model never saw the command or the carry-on


async def test_it_is_off_unless_switched_on(tmp_path):
    p, f = interrupted_story(tmp_path, ["tell me a story", "volume down"], [STORY, ["Volume down."]], resume=False)
    task = await interrupt(p, f)
    await wait_until(lambda: b"Volume down." in f.player.played)
    await asyncio.sleep(0.5)
    await finish(p, f, task)
    assert not any(b"genie" in x for x in f.player.played)  # nothing carried on by itself; "go on" still would


async def test_a_request_that_is_not_a_quick_command_does_not_resume_the_story(tmp_path):
    p, f = interrupted_story(tmp_path, ["tell me a story", "what is the capital of france"], [STORY, ["Paris."]])
    task = await interrupt(p, f)
    await wait_until(lambda: b"Paris." in f.player.played)
    await asyncio.sleep(0.5)
    await finish(p, f, task)
    assert not any(b"genie" in x for x in f.player.played)


async def test_an_old_interruption_is_not_brought_back(tmp_path):
    p, f = interrupted_story(tmp_path, ["tell me a story", "volume down"], [STORY, ["Volume down."]])
    task = await interrupt(p, f)
    await wait_until(lambda: b"Volume down." in f.player.played)
    await finish(p, f, task)
    p.voice.interrupted(["Stale leftover sentence."])
    p.voice._tail_at -= 600  # ten minutes ago
    assert p.voice.take_recent_tail() == []
    p.voice.interrupted(["Fresh sentence."])
    assert p.voice.take_recent_tail() == ["Fresh sentence."] and p.voice.take_recent_tail() == []  # taken once
