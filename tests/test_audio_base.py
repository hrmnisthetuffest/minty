import pytest
import io
import wave

import numpy as np

from minty_core.audio.endpointer import EnergyEndpointer, rms
from minty_core.audio.wav import pcm_to_wav
from minty_core.config import AudioConfig


def frame(amplitude, n=1280):
    return np.full(n, amplitude, dtype=np.int16).tobytes()


def endpointer(**kw):
    kw.setdefault("min_speech_frames", 1)
    cfg = AudioConfig(speech_rms_threshold=400, silence_ms=240, max_wait_ms=240, **kw)
    return EnergyEndpointer(lambda: cfg)


def test_rms():
    assert rms(frame(0)) == 0
    assert round(rms(frame(1000))) == 1000


def test_stops_after_silence_following_speech():
    e = endpointer()
    e.reset()
    assert e.feed(frame(2000)) is False and e.heard_speech
    assert [e.feed(frame(10)) for _ in range(3)] == [False, False, True]


def test_speech_resets_silence_counter():
    e = endpointer()
    e.reset()
    e.feed(frame(2000))
    e.feed(frame(10))
    e.feed(frame(10))
    assert e.feed(frame(2000)) is False
    assert [e.feed(frame(10)) for _ in range(3)] == [False, False, True]


def test_gives_up_when_nobody_speaks():  # RF2
    e = endpointer()
    e.reset()
    assert [e.feed(frame(10)) for _ in range(3)] == [False, False, True]
    assert e.heard_speech is False


def test_reset_clears_state():
    e = endpointer()
    e.reset()
    e.feed(frame(2000))
    e.reset()
    assert e.heard_speech is False


def test_pcm_to_wav_roundtrip():
    pcm = frame(123, 100)
    wav = pcm_to_wav(pcm, 16000)
    assert wav[:4] == b"RIFF"
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        assert w.readframes(100) == pcm



def test_a_blip_is_not_speech_but_a_held_sound_is():
    e = endpointer(min_speech_frames=3)
    e.reset()
    e.feed(frame(2000))
    e.feed(frame(2000))
    e.feed(frame(10))  # knock-knock, then quiet
    assert not e.heard_speech
    for _ in range(3):
        e.feed(frame(2000))
    assert e.heard_speech


def test_threshold_follows_the_room_noise():
    e = endpointer(noise_floor_factor=2.5)
    e.reset()
    for _ in range(100):
        e.feed(frame(300))  # a fan just under the fixed threshold
    assert 290 < e.noise_floor < 310 and e.threshold == pytest.approx(e.noise_floor * 2.5)
    e.reset()
    e.feed(frame(500))  # louder than the fixed 400, but not much louder than the fan
    assert not e.heard_speech
    e.feed(frame(2000))
    assert e.heard_speech
    assert e.noise_floor is not None  # survives reset


def test_noise_floor_can_be_switched_off():
    e = endpointer(noise_floor_factor=0)
    e.reset()
    for _ in range(50):
        e.feed(frame(300))
    assert e.threshold == 400
