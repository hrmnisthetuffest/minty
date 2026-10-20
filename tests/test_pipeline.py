import asyncio
from types import SimpleNamespace

import pytest

from tests.fakes import FakeAgent, FakeAudioIn, FakeEndpointer, FakePlayer, FakeSTT, FakeTTS, FakeWake
from minty_core.commands import CommandRouter
from minty_core.config import ConfigStore
from minty_core.events import EventBus
from minty_core.llm.types import LLMError
from minty_core.pipeline import VoicePipeline, is_affirmative


async def wait_until(pred, timeout=2.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


def build(tmp_path, *, stt_texts=(), replies=(), player_delay=0.0, tts=None, with_commands=False, follow_up=False,
          wake_ack=""):
    store = ConfigStore(tmp_path)
    data = store.load().model_dump(mode="json")
    data["audio"]["follow_up"] = follow_up
    data["audio"]["wake_ack"] = wake_ack  # most tests script exact frames; the acknowledgement has its own tests
    data["audio"]["barge_in"] = not follow_up  # the barge-in watcher would eat the pre-queued frames
    store.update(data)
    f = SimpleNamespace(
        audio=FakeAudioIn(), wake=FakeWake(), endpointer=FakeEndpointer(), stt=FakeSTT(stt_texts),
        tts=tts or FakeTTS(), player=FakePlayer(player_delay), agent=FakeAgent(replies), bus=EventBus(),
    )
    p = VoicePipeline(
        audio_in=f.audio, wake=f.wake, endpointer=f.endpointer, stt=f.stt, tts=f.tts,
        player=f.player, agent=f.agent, bus=f.bus, store=store,
        commands=CommandRouter(f.agent) if with_commands else None,
    )
    return p, f


async def finish(p, f, task):
    f.audio.close()
    await asyncio.wait_for(task, 2)


@pytest.mark.parametrize("text, expected", [
    ("yes please", True), ("Yeah, go ahead", True), ("no", False), ("no, don't do it", False),
    ("yes... actually no", False), ("banana", False), ("", False),
    ("not sure", False), ("I'm not okay with that", False), ("don\u2019t proceed", False), ("yes do it", True),
])
def test_is_affirmative(text, expected):
    assert is_affirmative(text) is expected


async def test_full_turn_speaks_each_sentence(tmp_path):
    p, f = build(tmp_path, stt_texts=["what time is it"], replies=[["It is noon.", "Anything else?"]])
    f.audio.push(b"x", b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: len(f.player.played) == 2)
    await finish(p, f, task)
    assert f.agent.heard == ["what time is it"]
    assert f.player.played == [b"It is noon.", b"Anything else?"]
    assert f.wake.resets >= 1 and p.state == "idle"


async def test_empty_transcript_is_ignored(tmp_path):  # RF1
    p, f = build(tmp_path, stt_texts=["   "], replies=[["never spoken"]])
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: len(f.stt.calls) == 1 and p.state == "idle")
    await finish(p, f, task)
    assert f.agent.heard == [] and f.player.played == []


async def test_silence_after_wake_never_reaches_stt(tmp_path):  # RF2
    p, f = build(tmp_path, stt_texts=[], replies=[])
    f.audio.push(b"WAKE", b"TIMEOUT")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: f.endpointer.feeds >= 1 and p.state == "idle")
    await finish(p, f, task)
    assert f.stt.calls == [] and f.agent.heard == [] and f.player.played == []


async def test_llm_failure_is_spoken_and_next_turn_works(tmp_path):  # RF4
    p, f = build(tmp_path, stt_texts=["one", "two"], replies=[LLMError("down"), ["Fine now."]])
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: b"I can't reach my brain right now." in f.player.played)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: b"Fine now." in f.player.played)
    await finish(p, f, task)


async def test_tts_failure_does_not_hang_the_turn(tmp_path):
    p, f = build(tmp_path, stt_texts=["a", "b"], replies=[["boom"], ["fine."]], tts=FakeTTS(fail_on={"boom"}))
    errors = f.bus.subscribe()
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: f.agent.heard == ["a"] and p.state == "idle")
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: b"fine." in f.player.played)
    await finish(p, f, task)
    assert any(e["type"] == "error" for e in list(errors._queue))


async def test_barge_in_interrupts_and_starts_a_new_turn(tmp_path):
    p, f = build(tmp_path, stt_texts=["first", "second"], replies=[["One.", "Two."], ["Reply two."]], player_delay=0.4)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: b"One." in f.player.played)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: b"Reply two." in f.player.played)
    await finish(p, f, task)
    assert b"Two." not in f.player.played
    assert f.player.stopped >= 1
    assert f.agent.heard == ["first", "second"]
    assert f.agent.closed >= 2


async def test_muted_pipeline_ignores_wake_word(tmp_path):
    p, f = build(tmp_path, stt_texts=["x"], replies=[["no"]])
    p.muted = True
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(f.audio.q.empty)
    await finish(p, f, task)
    assert f.stt.calls == [] and f.player.played == []


@pytest.mark.parametrize("answer, spoken", [("yes please", b"Done."), ("no way", b"Cancelled.")])
async def test_spoken_confirmation(tmp_path, answer, spoken):
    async def ask(confirm):
        return await confirm("Really do it?")

    p, f = build(tmp_path, stt_texts=["do the thing", answer], replies=[[ask]], player_delay=0.1)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: b"Really do it?" in f.player.played and p._watcher is None)
    f.audio.push(b"SPEECH", b"END")
    await wait_until(lambda: spoken in f.player.played)
    await finish(p, f, task)


async def test_say_plays_text(tmp_path):
    p, f = build(tmp_path)
    await p.say("testing")
    assert f.player.played == [b"testing"]


async def test_barge_in_closes_the_first_reply_before_the_next_turn(tmp_path):
    p, f = build(tmp_path, stt_texts=["first", "second"], replies=[["One.", "Two."], ["Reply two."]], player_delay=0.4)
    seen = []
    orig = f.agent.respond

    def respond(text, **kw):
        seen.append(f.agent.closed)
        return orig(text, **kw)

    f.agent.respond = respond
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: b"One." in f.player.played)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: b"Reply two." in f.player.played)
    await finish(p, f, task)
    assert seen[1] >= 1


async def test_cancel_during_confirm_leaks_no_watcher(tmp_path):
    async def ask(confirm):
        return await confirm("Really do it?")

    p, f = build(tmp_path, stt_texts=["do the thing"], replies=[[ask]])
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: b"Really do it?" in f.player.played and p._watcher is None)
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.05)
    assert p._watcher is None or p._watcher.done()
    assert not [t for t in asyncio.all_tasks() if t.get_coro().__qualname__.endswith("VoicePipeline._watch") and not t.done()]


async def test_notifications_are_spoken_when_idle(tmp_path):
    p, f = build(tmp_path)
    task = asyncio.create_task(p.run())
    f.bus.publish("notify", text="Your tea timer is done.")
    await wait_until(lambda: b"Your tea timer is done." in f.player.played)
    await finish(p, f, task)


async def test_notifications_wait_while_muted(tmp_path):
    p, f = build(tmp_path)
    p.muted = True
    task = asyncio.create_task(p.run())
    f.bus.publish("notify", text="Later.")
    await asyncio.sleep(0.4)
    assert f.player.played == []
    p.muted = False
    await wait_until(lambda: b"Later." in f.player.played)
    await finish(p, f, task)


async def test_notification_is_not_interrupted_by_a_wake_word(tmp_path):
    p, f = build(tmp_path, stt_texts=["hey"], replies=[["Reply."]], player_delay=0.3)
    task = asyncio.create_task(p.run())
    await asyncio.sleep(0.05)
    f.bus.publish("notify", text="Alert.")
    await wait_until(lambda: p.state == "speaking")
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: b"Alert." in f.player.played)
    await asyncio.sleep(0.4)
    await finish(p, f, task)
    assert f.agent.heard == []
    assert p.state == "idle"


async def test_run_unsubscribes_from_the_bus(tmp_path):
    p, f = build(tmp_path)
    task = asyncio.create_task(p.run())
    await asyncio.sleep(0.05)
    assert p._notices in f.bus._subs
    await finish(p, f, task)
    assert p._notices not in f.bus._subs


async def test_mode_command_is_spoken_and_never_reaches_the_agent(tmp_path):
    p, f = build(tmp_path, stt_texts=["Force thinking mode."], with_commands=True)
    events = f.bus.subscribe()
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: len(f.player.played) == 1)
    await finish(p, f, task)
    spoken = "Thinking mode on. I'll take longer but think harder."
    assert f.player.played == [spoken.encode()]
    assert f.agent.tier == "deep" and f.agent.heard == []
    seen = []
    while not events.empty():
        seen.append(events.get_nowait())
    transcripts = [(e["role"], e["text"]) for e in seen if e.get("type") == "transcript"]
    assert transcripts == [("user", "Force thinking mode."), ("assistant", spoken)]
    assert p.state == "idle"


async def test_ordinary_speech_still_reaches_the_agent_with_commands_wired(tmp_path):
    p, f = build(tmp_path, stt_texts=["I'm in normal mode today"], replies=[["Fine."]], with_commands=True)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: len(f.player.played) == 1)
    await finish(p, f, task)
    assert f.agent.heard == ["I'm in normal mode today"] and f.agent.tier == "fast"


async def test_follow_up_needs_no_second_wake_word(tmp_path):
    p, f = build(tmp_path, stt_texts=["who is the PM", "and of france"], replies=[["Modi."], ["Macron."]],
                 follow_up=True)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    f.audio.push(*([b"x"] * 10))  # settle frames after the first reply
    f.audio.push(b"SPEECH", b"END")  # follow-up, no WAKE
    task = asyncio.create_task(p.run())
    await wait_until(lambda: len(f.player.played) == 2)
    await finish(p, f, task)
    assert f.agent.heard == ["who is the PM", "and of france"]


async def test_follow_up_silence_returns_to_idle(tmp_path):
    p, f = build(tmp_path, stt_texts=["hi"], replies=[["Hello."]], follow_up=True)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    f.audio.push(*([b"x"] * 10))
    f.audio.push(b"TIMEOUT")  # nobody answers
    task = asyncio.create_task(p.run())
    await wait_until(lambda: len(f.player.played) == 1 and p.state == "idle")
    await finish(p, f, task)
    assert f.agent.heard == ["hi"]


async def test_words_after_the_wake_name_skip_recording_and_stt(tmp_path):
    p, f = build(tmp_path, stt_texts=[], replies=[["It is noon."]])
    f.wake.remainder = "what time is it"
    f.audio.push(b"WAKE")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: len(f.player.played) == 1)
    await finish(p, f, task)
    assert f.agent.heard == ["what time is it"] and f.stt.calls == []


async def test_level_and_live_caption_events(tmp_path):
    p, f = build(tmp_path, stt_texts=["hello world"], replies=[["Hi."]])
    p.captions = lambda pcm: "hello wor"
    original = f.audio.read

    async def slow_read():  # let the caption task run between frames, as a real mic would
        await asyncio.sleep(0.02)
        return await original()

    f.audio.read = slow_read
    q = f.bus.subscribe()
    f.audio.push(b"WAKE", b"SPEECH", *([b"xx"] * 14), b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: len(f.player.played) == 1)
    await finish(p, f, task)
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    assert any(e["type"] == "level" and "rms" in e and "speech" in e for e in events)
    shown = [e for e in events if e["type"] == "partial" and (e.get("text") or e.get("tentative"))]
    assert shown and all("hello wor".startswith((e["text"] + " " + e.get("tentative", "")).strip()) or
                         (e["text"] + " " + e.get("tentative", "")).strip() == "hello wor" for e in shown)
    assert any(e["text"] == "hello wor" for e in shown) or all(e["tentative"] == "hello wor" for e in shown)
    assert {"type": "partial", "text": ""} in events  # caption cleared when the final transcript lands


class FakeLive:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def available(self):
        return True

    async def converse(self, first_words, read_frame):
        self.calls.append(first_words)
        return self.ok


async def test_live_mode_handles_the_conversation(tmp_path):
    p, f = build(tmp_path, stt_texts=["never"], replies=[["never"]])
    p.live = FakeLive()
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: p.live.calls)
    await finish(p, f, task)
    assert f.agent.heard == [] and f.stt.calls == []


async def test_live_failure_falls_back_to_classic(tmp_path):
    p, f = build(tmp_path, stt_texts=["what time is it"], replies=[["Noon."]])
    p.live = FakeLive(ok=False)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: len(f.player.played) == 1)
    await finish(p, f, task)
    assert p.live.calls == [""] and f.agent.heard == ["what time is it"]


async def test_leaving_live_mode_mid_conversation_carries_on_in_classic(tmp_path):
    # "switch to mistral" in live mode: no wake word needed for the next question
    p, f = build(tmp_path, stt_texts=["what time is it"], replies=[["Noon."]])
    p.live = FakeLive()
    p.live.continue_classic = True
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: len(f.player.played) == 1)
    await finish(p, f, task)
    assert p.live.calls == [""] and f.agent.heard == ["what time is it"] and p.live.continue_classic is False


async def test_live_mode_command_in_classic_starts_live_without_the_wake_word(tmp_path):
    class Commands:
        switch_voice = None

        def handle(self, text):
            self.switch_voice = "live"
            return "Live mode on."

    p, f = build(tmp_path, stt_texts=["switch to live mode"], replies=[["never"]])
    p.commands, p.live = Commands(), FakeLive()
    p.live.ok = False  # the first live attempt fails, so classic handles the command ...
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: len(p.live.calls) >= 2)  # ... and then live starts again by itself
    await finish(p, f, task)
    assert p.commands.switch_voice is None and p.live.calls == ["", ""]


def test_dbfs_scale():
    from minty_core.pipeline import _dbfs

    assert _dbfs(32768) == 0.0 and _dbfs(0) == _dbfs(1) and -91 < _dbfs(1) < -90
    assert -21 < _dbfs(3277) < -19


async def test_restart_minty_speaks_first_then_restarts(tmp_path):
    p, f = build(tmp_path, stt_texts=["restart yourself"], replies=[["never"]])
    f.agent.tier, f.agent.force_private = "fast", False
    p.commands = CommandRouter(f.agent, restart=True)
    order = []
    original = f.player.play

    async def play(audio):
        order.append("spoke")
        await original(audio)

    f.player.play = play
    p.on_restart = lambda: order.append("restart")
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: "restart" in order)
    await finish(p, f, task)
    assert order == ["spoke", "restart"] and f.agent.heard == []


async def test_own_voice_picked_up_by_the_mic_is_ignored_and_commands_do_not_chain(tmp_path):
    # "Live mode on." is spoken, the follow-up window hears it back as speech, STT returns the same words
    p, f = build(tmp_path, stt_texts=["live mode on", "live mode on"], replies=[["never"]],
                 with_commands=True, follow_up=True)
    f.agent.tier, f.agent.force_private = "fast", False
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    f.audio.push(*([b"x"] * 12))
    f.audio.push(b"SPEECH", b"END")  # the echo
    task = asyncio.create_task(p.run())
    await wait_until(lambda: len(f.stt.calls) >= 1 and len(f.player.played) == 1)
    await asyncio.sleep(0.1)
    await finish(p, f, task)
    assert len(f.player.played) == 1  # spoken once, never twice


def test_echo_detection_matches_what_was_just_said_and_not_new_speech(tmp_path):
    p, f = build(tmp_path)
    p._remember_spoken("The weather in London is sunny and twenty four degrees.")
    assert p._is_echo("the weather in london is sunny")
    assert p._is_echo("The weather in London is sunny and twenty four degrees")
    assert not p._is_echo("what about Paris")
    assert not p._is_echo("")


async def test_shut_down_ends_the_conversation_without_a_follow_up_window(tmp_path):
    p, f = build(tmp_path, stt_texts=["shut down", "this must not be heard"], replies=[["never"]],
                 with_commands=True, follow_up=True)
    f.agent.tier, f.agent.force_private = "fast", False
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    f.audio.push(*([b"x"] * 12), b"SPEECH", b"END")  # someone talks afterwards, without the wake word
    task = asyncio.create_task(p.run())
    await wait_until(lambda: len(f.player.played) == 1 and p.state == "idle")
    await asyncio.sleep(0.1)
    await finish(p, f, task)
    assert len(f.stt.calls) == 1 and f.agent.heard == []  # back to waiting for "Minty"


class TurnTracer:
    def __init__(self):
        self.events = []

    def event(self, kind, **data):
        self.events.append({"kind": kind, **data})


async def test_each_turn_is_traced_with_latency_from_end_of_speech(tmp_path):
    p, f = build(tmp_path, stt_texts=["what time is it"], replies=[["It is noon.", "Anything else?"]])
    p.tracer = TurnTracer()
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: len(f.player.played) == 2)
    await finish(p, f, task)
    turns = [e for e in p.tracer.events if e["kind"] == "turn"]
    assert turns[0]["outcome"] == "answered" and turns[0]["mode"] == "classic" and turns[0]["words"] == 4
    t = turns[0]
    assert 0 <= t["stt_ms"] <= t["first_sentence_ms"] <= t["first_audio_ms"] <= t["total_ms"]


async def test_commands_and_silence_are_traced_with_their_outcome(tmp_path):
    p, f = build(tmp_path, stt_texts=["thinking mode"], replies=[["never"]], with_commands=True)
    p.tracer = TurnTracer()
    f.agent.tier, f.agent.force_private = "fast", False
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: len(f.player.played) == 1)
    await finish(p, f, task)
    outcomes = [e["outcome"] for e in p.tracer.events if e["kind"] == "turn"]
    assert outcomes[0] == "command"


async def test_notices_during_a_live_conversation_go_into_it(tmp_path):
    p, f = build(tmp_path, stt_texts=[], replies=[])
    injected = []

    class Live:
        async def inject(self, text):
            injected.append(text)
            return True

    p.live = Live()
    task = asyncio.create_task(p.run())
    await asyncio.sleep(0.05)
    p._set_state("live")
    f.bus.publish("notify", text="The lamp search is done.")
    await wait_until(lambda: injected)
    assert injected == ["The lamp search is done."] and f.player.played == []  # no second voice
    p._set_state("idle")
    await finish(p, f, task)


async def test_noise_never_reaches_the_agent_and_is_counted(tmp_path):
    p, f = build(tmp_path, stt_texts=["Thanks for watching!", "what time is it"], replies=[["It is noon."]])
    voiced = iter([0, 300, 1500])  # fan noise; a stock phrase from a short sound; real speech
    p.speech_check = lambda pcm: next(voiced)
    p.tracer = TurnTracer()
    task = asyncio.create_task(p.run())
    for _ in range(3):
        f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: len(f.player.played) == 1)
    await finish(p, f, task)
    assert f.agent.heard == ["what time is it"] and p.noise_ignored == 2
    noise = [e for e in p.tracer.events if e["kind"] == "turn" and e["outcome"] == "noise"]
    assert [e["reason"] for e in noise][0] == "only 0 ms of speech" and "mis-hearing" in noise[1]["reason"]
    assert len(f.stt.calls) == 2  # the 0 ms recording was never transcribed



async def test_a_bare_wake_word_is_acknowledged_from_a_cached_clip_then_it_listens(tmp_path):
    p, f = build(tmp_path, stt_texts=["what time is it"], replies=[["It is noon."]], wake_ack="Yes, sir?")
    await p.warm_up()
    synthesised = list(f.tts.calls) if hasattr(f.tts, "calls") else None
    states = []
    original = p._set_state
    p._set_state = lambda s: (states.append(s), original(s))[1]
    # the acknowledgement takes ~0.7 s of audio: what the mic hears then is dropped, the command after it is kept
    f.audio.push(b"WAKE", *([b"echo"] * 12), b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: len(f.player.played) == 2)
    await finish(p, f, task)
    assert f.player.played == [b"Yes, sir?", b"It is noon."] and f.agent.heard == ["what time is it"]
    assert states[:3] == ["speaking", "listening", "processing"]
    if synthesised is not None:
        assert f.tts.calls.count("Yes, sir?") == 1  # synthesised once at start, not on every wake


async def test_a_wake_word_with_a_command_goes_straight_to_work_without_the_acknowledgement(tmp_path):
    p, f = build(tmp_path, stt_texts=[], replies=[["It is noon."]], wake_ack="Yes, sir?")
    f.wake.remainder = "what time is it"
    f.audio.push(b"WAKE")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: len(f.player.played) == 1)
    await finish(p, f, task)
    assert f.player.played == [b"It is noon."]


async def test_normal_mode_shows_processing_and_only_thinking_mode_shows_thinking(tmp_path):
    for tier, expected in (("fast", "processing"), ("deep", "thinking")):
        p, f = build(tmp_path / tier, stt_texts=["hello"], replies=[["Hi."]])
        f.agent.tier = tier
        states = []
        original = p._set_state
        p._set_state = lambda s, original=original, states=states: (states.append(s), original(s))[1]
        f.audio.push(b"WAKE", b"SPEECH", b"END")
        task = asyncio.create_task(p.run())
        await wait_until(lambda: len(f.player.played) == 1)
        await finish(p, f, task)
        assert expected in states and ({"thinking", "processing"} - {expected}).isdisjoint(states)


async def test_a_running_tool_shows_executing_then_back_to_processing(tmp_path):
    p, f = build(tmp_path)
    p.state = "processing"
    p._on_tool("browser_task")
    assert p.state == "executing"
    p._on_tool(None)
    assert p.state == "processing"
    p.state = "speaking"
    p._on_tool("x")
    assert p.state == "speaking"  # a tool started by something else never hijacks speech


async def test_the_trace_records_how_long_wake_to_listening_and_speech_capture_took(tmp_path):
    p, f = build(tmp_path, stt_texts=["hello"], replies=[["Hi."]])
    events = []
    p.tracer = SimpleNamespace(event=lambda kind, **data: events.append((kind, data)))
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: len(f.player.played) == 1)
    await finish(p, f, task)
    turn = next(d for k, d in events if k == "turn")
    assert turn["wake_to_listen_ms"] >= 0 and turn["capture_ms"] >= 0 and turn["stt_ms"] is not None
