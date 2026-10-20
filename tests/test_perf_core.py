"""Performance core (W0-33): warm connections, ready-made stock phrases, stall stacks, offline model load and a
lighter start."""
import asyncio
import subprocess
import sys
import time
import types
from pathlib import Path

from tests.test_pipeline import build, finish, wait_until
from tests.test_turn_deadline import DelayedAgent, set_turn
from minty_core import httpclient
from minty_core.loopwatch import watch
from minty_core.pipeline import BRAIN_DOWN, MODELS_SLOW, ONE_MOMENT, STILL_WORKING

ROOT = Path(__file__).resolve().parents[1]


async def test_connections_stay_warm_between_turns():
    httpclient._client = None
    client = httpclient.get_client()
    assert httpclient.KEEPALIVE_S == 60.0
    pool = client._transport._pool
    assert pool._keepalive_expiry == 60.0
    await client.aclose()
    httpclient._client = None


async def test_stock_phrases_are_synthesised_once_at_start(tmp_path):
    p, f = build(tmp_path, stt_texts=["slow one"])
    await p.warm_up()
    made = list(f.tts.calls)
    for phrase in (ONE_MOMENT, STILL_WORKING, MODELS_SLOW, BRAIN_DOWN):
        assert phrase in made
    p.agent = f.agent = DelayedAgent([[(0.3, "Done.")]])
    set_turn(p, notice_after_s=0.1)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: b"Done." in f.player.played)
    await finish(p, f, task)
    assert ONE_MOMENT.encode() in f.player.played
    assert f.tts.calls.count(ONE_MOMENT) == 1  # played from the ready-made clip, not synthesised again


async def test_a_voice_change_makes_new_clips(tmp_path):
    p, f = build(tmp_path)
    await p.warm_up()
    data = p.store.config.model_dump(mode="json")
    data["tts"]["speed"] = 1.3
    p.store.update(data)
    await p._stock_audio(ONE_MOMENT)
    assert f.tts.calls.count(ONE_MOMENT) == 2


class Recorder:
    def __init__(self):
        self.events = []

    def event(self, kind, **data):
        self.events.append((kind, data))


def blocking_helper_that_freezes_the_loop():
    time.sleep(0.45)


async def test_a_stall_is_traced_with_what_was_running():
    tracer = Recorder()
    task = asyncio.create_task(watch(tracer, tick=0.05))
    await asyncio.sleep(0.12)
    blocking_helper_that_freezes_the_loop()
    await asyncio.sleep(0.15)
    task.cancel()
    stalls = [d for k, d in tracer.events if k == "loop_stall"]
    assert stalls and stalls[0]["ms"] >= 300
    assert "blocking_helper_that_freezes_the_loop" in stalls[0]["stack"]


def test_cached_whisper_models_load_without_asking_the_network(monkeypatch):
    from minty_core.audio import stt
    calls = []

    class FakeModel:
        def __init__(self, name, **kw):
            calls.append(kw.get("local_files_only"))
            if kw.get("local_files_only") and name == "not-cached":
                raise RuntimeError("not in the cache")

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=FakeModel))
    monkeypatch.setattr(stt, "_MODELS", {})
    stt._load_whisper("base.en")
    assert calls == [True]
    calls.clear()
    stt._load_whisper("not-cached")
    assert calls == [True, False]  # downloaded once the normal way


def test_importing_the_app_no_longer_loads_the_web_api():
    code = "import sys, minty_core.app; print('minty_core.api' in sys.modules, 'fastapi' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT,
                         env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"}).stdout.split()
    assert out == ["False", "False"]


async def test_the_api_waits_for_the_voice_but_not_forever():
    from minty_core.app import wait_for_voice
    ready = asyncio.Event()
    ctx = types.SimpleNamespace(pipeline=types.SimpleNamespace(ready=ready))
    asyncio.get_running_loop().call_later(0.05, ready.set)
    started = time.monotonic()
    assert await wait_for_voice(ctx, timeout=2.0) is True and time.monotonic() - started < 1.0
    stuck = types.SimpleNamespace(pipeline=types.SimpleNamespace(ready=asyncio.Event()))
    assert await wait_for_voice(stuck, timeout=0.05) is False
    assert await wait_for_voice(types.SimpleNamespace(pipeline=None), timeout=5) is False


def _deep(n, out):
    if n:
        return _deep(n - 1, out)
    import sys
    out.append(sys._getframe())


def test_the_stall_stack_keeps_the_innermost_frames_and_stays_short_whatever_the_path_length():
    """The stack was cut from the end, so a long install path pushed the frame that mattered out of the trace."""
    from minty_core.loopwatch import compact_stack
    frames = []
    _deep(12, frames)
    text = compact_stack(frames[0], frames=6, limit=280)
    assert len(text) <= 280 and text.endswith("_deep") or "_deep" in text.split(" > ")[-1]
    assert "test_perf_core.py:" in text  # file names without their folders
    assert text.count(" > ") <= 5  # at most six frames
    tiny = compact_stack(frames[0], frames=6, limit=40)
    assert len(tiny) <= 40 and "_deep" in tiny  # when space is short the outer frames go first
