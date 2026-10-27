"""The core Docker image has no voice libraries on purpose: it must start quietly without voice, not crash a loop six times."""
import asyncio
import importlib.util
import logging
from types import SimpleNamespace

from minty_core import app


def hide(monkeypatch, name):
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda n, *a, **k: None if n == name else real(n, *a, **k))


def test_a_missing_voice_library_is_named(monkeypatch):
    hide(monkeypatch, "sounddevice")
    assert app.voice_libraries_missing() == "sounddevice"


def test_with_the_library_there_nothing_is_missing(monkeypatch):
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda n, *a, **k: object() if n == "sounddevice" else real(n, *a, **k))
    assert app.voice_libraries_missing() == ""


async def test_voice_is_skipped_quietly_and_the_rest_may_start(monkeypatch, caplog):
    hide(monkeypatch, "sounddevice")
    states = []
    pipeline = SimpleNamespace(ready=asyncio.Event(), _set_state=states.append)
    ctx = SimpleNamespace(pipeline=pipeline, services={}, store=None, bus=None)
    with caplog.at_level(logging.INFO, logger="minty"):
        await asyncio.wait_for(app._start_voice(ctx), 2)  # returns at once instead of loading models and retrying
    assert pipeline.ready.is_set()  # the panel and the control API are not left waiting for a voice that will never come
    assert "voice_supervisor" not in ctx.services and states == []
    assert any("voice is off" in r.getMessage() and "sounddevice" in r.getMessage() for r in caplog.records)


def test_a_missing_speech_library_is_one_quiet_line_but_a_real_failure_keeps_its_traceback(monkeypatch, caplog):
    from minty_core.audio import vad

    def missing():
        raise ModuleNotFoundError("No module named 'faster_whisper'")

    def broken():
        raise RuntimeError("model file is damaged")

    with caplog.at_level(logging.WARNING, logger="minty_core.audio.vad"):
        monkeypatch.setattr(vad, "FrameVAD", missing)
        assert vad.build_frame_vad() is None
        quiet = [r for r in caplog.records if "speech detector unavailable" in r.getMessage()]
        assert len(quiet) == 1 and quiet[0].exc_info is None and "faster_whisper" in quiet[0].getMessage()
        caplog.clear()
        monkeypatch.setattr(vad, "FrameVAD", broken)
        assert vad.build_frame_vad() is None
        loud = [r for r in caplog.records if "speech detector unavailable" in r.getMessage()]
        assert len(loud) == 1 and loud[0].exc_info is not None
