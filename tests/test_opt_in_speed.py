"""Five speed-ups that are off until you turn them on: a stable prompt order, a lean prompt, barge-in during a Live answer, a recogniser
sized to the computer, speech made ahead, and a warm connection when the name is heard. Each keeps today's behaviour when it is off."""
import asyncio
import os
import time

import httpx
import pytest

from minty_core import promptlib
from minty_core.config import AudioConfig, Config
from minty_core.context import build_context
from tests.test_pipeline import build, finish, wait_until


def set_config(target, **sections):
    """Change settings of a running store: set_config(store, turn={"stable_prefix": True})."""
    data = target.config.model_dump(mode="json")
    for section, values in sections.items():
        data[section].update(values)
    target.update(data)


def test_everything_is_off_by_default():
    cfg = Config()
    assert cfg.turn.stable_prefix is False and cfg.turn.lean_prompt is False
    assert cfg.audio.live_barge_in is False and cfg.audio.live_barge_in_rms == 3000
    assert cfg.stt.auto_model is False and cfg.tts.parallel == 1 and cfg.router.warm_on_wake is False


# ---- 1. a stable prompt order, and a lean prompt ----------------------------------------------------------
def library_text() -> str:
    return promptlib.render(promptlib.select(channel="voice", tier="fast"))


def test_stable_prefix_puts_the_notes_you_change_after_everything_that_does_not_change(tmp_path):
    ctx = build_context(tmp_path)
    marker = library_text().strip()[:40]
    ctx.memory.add("likes green tea")
    before = ctx.agent._system_prompt("hello")
    assert before.index("likes green tea") < before.index(marker)  # today: notes sit before the prompt library
    set_config(ctx.store, turn={"stable_prefix": True})
    after = ctx.agent._system_prompt("hello")
    assert after.index("likes green tea") > after.index(marker)
    ctx.memory.add("lives by the sea")  # a new note changes only what comes after the prefix
    changed = ctx.agent._system_prompt("hello")
    prefix = after[:after.index("Things you remember")]
    assert changed.startswith(prefix) and "lives by the sea" in changed
    assert sorted(after.split("\n\n")) == sorted(before.split("\n\n"))  # the same text, only in a steadier order


def test_stable_prefix_with_no_notes_changes_nothing(tmp_path):
    ctx = build_context(tmp_path)
    before = ctx.agent._system_prompt("hello")
    set_config(ctx.store, turn={"stable_prefix": True})
    assert ctx.agent._system_prompt("hello") == before


def test_the_lean_prompt_uses_the_short_wording_of_the_fixed_rules(tmp_path):
    from minty_core.prompts import _TERSE
    ctx = build_context(tmp_path)
    full = ctx.agent._system_prompt("hello")
    set_config(ctx.store, turn={"lean_prompt": True})
    lean = ctx.agent._system_prompt("hello")
    assert len(lean) < len(full) - 150
    assert _TERSE["sections"]["BASE_RULES"] in lean
    set_config(ctx.store, turn={"lean_prompt": False})
    assert ctx.agent._system_prompt("hello") == full


# ---- 2. barge-in while a Live answer is playing ---------------------------------------------------------------
def frame(level: int) -> bytes:
    import numpy as np
    return np.full(1280, level, dtype=np.int16).tobytes()


def test_the_microphone_is_muted_while_live_speaks_unless_barge_in_is_on_and_you_are_loud():
    from minty_core.live import guard_frame
    quiet, loud = frame(800), frame(6000)  # the room's echo of the voice; you talking over it
    off = AudioConfig()
    assert guard_frame(quiet, True, off) == bytes(len(quiet)) and guard_frame(loud, True, off) == bytes(len(loud))  # today
    on = AudioConfig(live_barge_in=True)
    assert guard_frame(quiet, True, on) == bytes(len(quiet))  # an echo is still blocked
    assert guard_frame(loud, True, on) == loud  # a voice over it goes through, so Gemini can stop talking
    assert guard_frame(quiet, False, on) == quiet and guard_frame(quiet, False, off) == quiet  # when it is silent, all passes
    assert guard_frame(loud, True, AudioConfig(live_barge_in=True, live_echo_guard=False)) == loud


async def test_the_send_loop_applies_the_guard(tmp_path):
    from types import SimpleNamespace

    from minty_core.config import ConfigStore
    from minty_core.live import LiveMode

    store = ConfigStore(tmp_path)
    set_config(store, audio={"live_barge_in": True})
    live = LiveMode(store, None, None, None, None, None)
    sent = []

    class Session:
        async def send_realtime_input(self, audio):
            sent.append(audio.data)

    frames = [frame(800), frame(6000), frame(800)]

    async def read():
        if not frames:
            raise EOFError
        return frames.pop(0)

    state = SimpleNamespace(speaking_until=time.monotonic() + 60)
    with pytest.raises(EOFError):
        await live._send(Session(), read, state)
    assert [d == bytes(len(d)) for d in sent] == [True, False, True]


# ---- 3. a recogniser sized to the computer ------------------------------------------------------------------
def hardware(ram_gb=16, threads=8, flags=("avx2", "sse2"), gpus=()):
    from minty_core.hwprobe import Hardware
    return Hardware("cpu", threads, frozenset(flags), ram_gb * 1024, ram_gb * 1024, 0, False, tuple(gpus))


def test_the_recogniser_size_follows_the_computer_only_when_asked():
    from minty_core.config import SttConfig
    from minty_core.hwprobe import Gpu, pick_stt_model
    from minty_core.audio.stt import effective_local_model
    assert pick_stt_model(hardware(ram_gb=4, threads=2)) == "tiny.en"  # a small computer
    assert pick_stt_model(hardware(ram_gb=8, threads=4, flags=("sse2",))) == "tiny.en"  # no AVX2: larger models are slow
    assert pick_stt_model(hardware(ram_gb=16, threads=8)) == "base.en"
    assert pick_stt_model(hardware(gpus=[Gpu("nvidia", "RTX", 8000)])) == "small.en"
    assert pick_stt_model(hardware(ram_gb=0)) is None  # unknown memory: no guess
    cfg = SttConfig(local_model="medium.en")
    assert effective_local_model(cfg, hw=hardware(ram_gb=4)) == "medium.en"  # off: what you set
    on = SttConfig(local_model="medium.en", auto_model=True)
    assert effective_local_model(on, hw=hardware(ram_gb=4, threads=2)) == "tiny.en"
    assert effective_local_model(on, hw=hardware(ram_gb=0)) == "medium.en"  # unknown: what you set


# ---- 4. speech made ahead -----------------------------------------------------------------------------------
class SlowTTS:
    """Each sentence takes 0.15 s to speak into audio; remembers how many were being made at once."""

    def __init__(self):
        self.calls, self.running, self.most = [], 0, 0

    async def synthesize(self, text):
        from minty_core.audio.types import Audio
        self.calls.append(text)
        self.running += 1
        self.most = max(self.most, self.running)
        await asyncio.sleep(0.15)
        self.running -= 1
        return Audio(text.encode(), 16000)


async def speak(tmp_path, parallel):
    p, f = build(tmp_path, stt_texts=["go"], replies=[["One.", "Two.", "Three.", "Four."]], tts=SlowTTS())
    set_config(p.store, tts={"parallel": parallel})
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    started = time.monotonic()
    await wait_until(lambda: len(f.player.played) == 4, timeout=5)
    took = time.monotonic() - started
    await finish(p, f, task)
    return f, took


async def test_sentences_are_made_one_at_a_time_by_default(tmp_path):
    f, _ = await speak(tmp_path, 1)
    assert f.tts.most == 1 and f.player.played == [b"One.", b"Two.", b"Three.", b"Four."]


async def test_with_parallel_two_the_next_sentence_is_made_while_the_last_is_still_being_made_and_the_order_holds(tmp_path):
    f, took = await speak(tmp_path, 2)
    assert f.tts.most == 2
    assert f.player.played == [b"One.", b"Two.", b"Three.", b"Four."]  # never out of order
    serial, took_serial = await speak(tmp_path / "again", 1)
    assert took < took_serial  # and sooner


async def test_a_failed_sentence_ends_the_turn_cleanly_when_made_ahead_and_the_next_turn_works(tmp_path):
    class Flaky(SlowTTS):
        async def synthesize(self, text):
            if text == "Two.":
                raise RuntimeError("voice failed")
            return await super().synthesize(text)

    p, f = build(tmp_path, stt_texts=["go", "again"], replies=[["One.", "Two.", "Three."], ["Fine."]], tts=Flaky())
    set_config(p.store, tts={"parallel": 2})
    task = asyncio.create_task(p.run())
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: p.state == "idle" and f.player.played[:1] == [b"One."], timeout=5)  # like one at a time: it stops there
    assert b"Three." not in f.player.played
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: b"Fine." in f.player.played, timeout=5)
    await finish(p, f, task)


# ---- 5. a warm connection when the name is heard --------------------------------------------------------------
async def test_warm_opens_one_connection_per_provider_address_without_sending_a_prompt(tmp_path):
    ctx = build_context(tmp_path)
    ctx.secrets.set("groq", "gsk_abcdefghijklmnop")
    seen = []

    def handler(request):
        seen.append((request.method, str(request.url), "authorization" in request.headers))
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    warmed = await ctx.router.warm(client)
    assert warmed >= 1 and seen and all(method == "HEAD" for method, _, _ in seen)
    assert len({url for _, url, _ in seen}) == len(seen)  # one per address
    assert not any(auth for _, _, auth in seen)  # no key is sent just to open a connection


async def test_warm_never_raises_when_the_network_is_down(tmp_path):
    ctx = build_context(tmp_path)
    ctx.secrets.set("groq", "gsk_abcdefghijklmnop")

    def handler(request):
        raise httpx.ConnectError("down")

    assert await ctx.router.warm(httpx.AsyncClient(transport=httpx.MockTransport(handler))) == 0


async def test_hearing_the_name_warms_the_connection_only_when_switched_on(tmp_path):
    calls = []

    async def warm():
        calls.append(1)

    p, f = build(tmp_path, stt_texts=["a", "b"], replies=[["One."], ["Two."]])
    p.warm = warm
    task = asyncio.create_task(p.run())
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: b"One." in f.player.played)
    assert calls == []  # off
    set_config(p.store, router={"warm_on_wake": True})
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: b"Two." in f.player.played)
    await asyncio.sleep(0.05)
    assert calls == [1]
    await finish(p, f, task)
