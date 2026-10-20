"""Hearing the turn during the pause: the guess at what was said starts after a short silence, so by the time the pause is
long enough to end the turn the words are ready, and a sentence that is plainly finished can end the turn early."""
import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from minty_core.audio.endpointer import EnergyEndpointer
from minty_core.config import AudioConfig, ConfigStore
from minty_core.events import EventBus
from minty_core.pipeline import VoicePipeline
from minty_core.voice.turnhold import looks_complete
from tests.fakes import FakeAgent, FakeAudioIn, FakeEndpointer, FakePlayer, FakeTTS, FakeWake


class QuietEndpointer(FakeEndpointer):
    """SPEECH is speech; QUIET is one silent frame (80 ms); END is the silent frame that ends the turn."""

    def __init__(self):
        super().__init__()
        self._quiet = 0

    def reset(self):
        super().reset()
        self._quiet = 0

    def feed(self, frame):
        self.feeds += 1
        if frame == b"SPEECH":
            self.heard_speech, self._quiet = True, 0
            return False
        if self.heard_speech:
            self._quiet += 1
        return frame in (b"END", b"TIMEOUT")

    @property
    def quiet_ms(self):
        return self._quiet * 80 if self.heard_speech else 0


class Ears:
    """A recogniser that takes a little time and remembers when it was asked (in endpointer frames) and with what audio."""

    def __init__(self, texts, endpointer, delay=0.01, fail_first=False):
        self.texts, self.endpointer, self.delay, self.fail_first = list(texts), endpointer, delay, fail_first
        self.calls, self.feeds_at_call = [], []

    async def transcribe(self, pcm, rate=16000):
        self.calls.append(pcm)
        self.feeds_at_call.append(self.endpointer.feeds)
        text = self.texts.pop(0)  # taken at once: a recognition that is cancelled still used up its words
        await asyncio.sleep(self.delay)
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("recogniser hiccup")
        return text


class Events:
    def __init__(self):
        self.items = []

    def event(self, kind, **fields):
        self.items.append((kind, fields))

    def turns(self):
        return [f for kind, f in self.items if kind == "turn"]


def build(tmp_path, texts, replies=(("ok.",),), delay=0.01, fail_first=False, **audio):
    store = ConfigStore(tmp_path)
    data = store.load().model_dump(mode="json")
    data["audio"].update(follow_up=False, wake_ack="", barge_in=False, **audio)
    store.update(data)
    endpointer = QuietEndpointer()
    ears = Ears(texts, endpointer, delay, fail_first)
    f = SimpleNamespace(audio=FakeAudioIn(), wake=FakeWake(), endpointer=endpointer, stt=ears, tts=FakeTTS(),
                        player=FakePlayer(), agent=FakeAgent([list(r) for r in replies]), bus=EventBus())
    p = VoicePipeline(audio_in=f.audio, wake=f.wake, endpointer=endpointer, stt=ears, tts=f.tts, player=f.player,
                      agent=f.agent, bus=f.bus, store=store)
    p.tracer = Events()
    return p, f


async def until(pred, timeout=2.0):
    end = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


async def feed(f, *frames, gap=0.004):
    """Frames arrive one at a time, as a microphone delivers them."""
    for frame in frames:
        f.audio.push(frame)
        await asyncio.sleep(gap)


async def stop(p, f, task):
    f.audio.close()
    await asyncio.wait_for(task, 2)


async def test_the_words_are_ready_when_the_pause_is_long_enough_to_end_the_turn(tmp_path):
    p, f = build(tmp_path, ["What time is it."], early_finish_ms=0)
    task = asyncio.create_task(p.run())
    await feed(f, b"WAKE", b"SPEECH", *[b"QUIET"] * 5)
    await until(lambda: len(f.stt.calls) == 1)
    assert f.stt.feeds_at_call == [6]  # asked after 400 ms of silence, not after the turn ended
    await feed(f, b"QUIET", b"QUIET", b"END")
    await until(lambda: f.agent.heard == ["What time is it."])
    await stop(p, f, task)
    assert len(f.stt.calls) == 1  # the guess was the answer: one recognition, not two
    assert p.tracer.turns()[0]["spec"] == "used"


async def test_the_guess_is_shown_as_the_caption_while_the_pause_lasts(tmp_path):
    p, f = build(tmp_path, ["What time is it."], early_finish_ms=0)
    seen = f.bus.subscribe({"partial"})
    task = asyncio.create_task(p.run())
    await feed(f, b"WAKE", b"SPEECH", *[b"QUIET"] * 5)
    for _ in range(40):  # frames keep arriving every 80 ms while the pause lasts
        if any(e.get("text") == "What time is it." for e in list(seen._queue)):
            break
        await feed(f, b"QUIET", gap=0.02)
    assert any(e.get("text") == "What time is it." for e in list(seen._queue))
    await feed(f, b"END")
    await until(lambda: f.agent.heard == ["What time is it."])
    await stop(p, f, task)


async def test_speech_that_carries_on_throws_the_guess_away(tmp_path):
    p, f = build(tmp_path, ["what is", "What is the weather like."], early_finish_ms=0)
    task = asyncio.create_task(p.run())
    await feed(f, b"WAKE", b"SPEECH", *[b"QUIET"] * 5)
    await until(lambda: len(f.stt.calls) == 1)
    await feed(f, b"SPEECH", b"SPEECH", b"QUIET", b"END")
    await until(lambda: f.agent.heard == ["What is the weather like."])
    await stop(p, f, task)
    assert len(f.stt.calls) == 2 and f.stt.calls[0].count(b"SPEECH") == 1 and f.stt.calls[1].count(b"SPEECH") == 3
    assert p.tracer.turns()[0].get("spec") is None


async def test_a_finished_sentence_ends_the_turn_early(tmp_path):
    p, f = build(tmp_path, ["What time is it?"], early_finish_ms=480)
    task = asyncio.create_task(p.run())
    await feed(f, b"WAKE", b"SPEECH", *[b"QUIET"] * 8)  # no END: the long pause never comes
    await until(lambda: f.agent.heard == ["What time is it?"])
    await stop(p, f, task)
    turn = p.tracer.turns()[0]
    assert turn["spec"] == "early" and 480 <= turn["silence_ms"] < 900


@pytest.mark.parametrize("said", ["I want to know about", "So the plan is,", "I was going to.", "and then...", "what time is it"])
async def test_a_sentence_that_may_go_on_waits_for_the_normal_pause(tmp_path, said):
    p, f = build(tmp_path, [said], early_finish_ms=480)
    task = asyncio.create_task(p.run())
    await feed(f, b"WAKE", b"SPEECH", *[b"QUIET"] * 9)
    await asyncio.sleep(0.05)
    assert f.agent.heard == []  # not cut off
    await feed(f, b"END")
    await until(lambda: f.agent.heard == [said])
    await stop(p, f, task)
    assert p.tracer.turns()[0]["spec"] == "used"


async def test_it_can_be_switched_off(tmp_path):
    p, f = build(tmp_path, ["What time is it?"], speculative_stt=False, early_finish_ms=0)
    task = asyncio.create_task(p.run())
    await feed(f, b"WAKE", b"SPEECH", *[b"QUIET"] * 7)
    await asyncio.sleep(0.05)
    assert f.stt.calls == []  # nothing is recognised until the turn is over
    await feed(f, b"END")
    await until(lambda: f.agent.heard == ["What time is it?"])
    await stop(p, f, task)
    turn = p.tracer.turns()[0]
    assert turn.get("spec") is None and f.stt.feeds_at_call == [9]


async def test_a_failed_guess_is_not_a_failed_turn(tmp_path):
    p, f = build(tmp_path, ["a guess that fails", "What time is it."], early_finish_ms=0, fail_first=True)
    task = asyncio.create_task(p.run())
    await feed(f, b"WAKE", b"SPEECH", *[b"QUIET"] * 5)
    await until(lambda: len(f.stt.calls) == 1)
    await feed(f, b"QUIET", b"END")
    await until(lambda: f.agent.heard == ["What time is it."])
    await stop(p, f, task)
    assert len(f.stt.calls) == 2


async def test_noise_never_leaves_a_guess_running_into_the_next_turn(tmp_path):
    p, f = build(tmp_path, ["thank you", "What time is it."], early_finish_ms=0)
    p.speech_check = lambda pcm: 10  # far less speech than min_voiced_ms: this is noise
    task = asyncio.create_task(p.run())
    await feed(f, b"WAKE", b"SPEECH", *[b"QUIET"] * 5)
    await until(lambda: len(f.stt.calls) == 1)
    await feed(f, b"END")
    await until(lambda: p.noise_ignored == 1)
    assert p._spec is None
    p.speech_check = None
    await feed(f, b"WAKE", b"SPEECH", *[b"QUIET"] * 5, b"END")
    await until(lambda: f.agent.heard == ["What time is it."])
    await stop(p, f, task)


async def test_old_style_endpointers_without_a_quiet_clock_work_as_before(tmp_path):
    p, f = build(tmp_path, ["hello there"], early_finish_ms=480)
    p.endpointer = FakeEndpointer()  # no quiet_ms
    f.endpointer = p.endpointer
    task = asyncio.create_task(p.run())
    await feed(f, b"WAKE", b"SPEECH", b"END")
    await until(lambda: f.agent.heard == ["hello there"])
    await stop(p, f, task)


@pytest.mark.parametrize("text, complete", [
    ("What time is it?", True), ("Stop.", True), ("Turn on the light!", True), ("He said \"go.\"", True),
    ("", False), ("what time is it", False), ("I think that...", False), ("and then,", False), ("I was going to.", False),
    ("Well, um.", False), ("Because.", False),
])
def test_what_counts_as_a_finished_sentence(text, complete):
    assert looks_complete(text) is complete


def test_the_real_endpointer_tells_how_long_it_has_been_quiet():
    loud = np.full(1280, 2000, dtype=np.int16).tobytes()
    quiet = np.full(1280, 10, dtype=np.int16).tobytes()
    cfg = AudioConfig(speech_rms_threshold=400, silence_ms=960, max_wait_ms=960, min_speech_frames=1)
    e = EnergyEndpointer(lambda: cfg)
    e.reset()
    assert e.quiet_ms == 0
    e.feed(quiet)
    assert e.quiet_ms == 0  # before any speech there is no pause to measure
    e.feed(loud)
    assert e.quiet_ms == 0
    for n in range(1, 4):
        e.feed(quiet)
        assert e.quiet_ms == n * 80
    e.feed(loud)
    assert e.quiet_ms == 0
