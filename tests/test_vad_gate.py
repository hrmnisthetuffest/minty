"""The speech detector says 'no speech' for noise and 'speech' for speech."""
import wave
from pathlib import Path

import numpy as np
import pytest

from minty_core.audio.vad import voiced_ms

SR = 16000
FIXTURES = Path(__file__).parent / "fixtures"


def pcm(a: np.ndarray) -> bytes:
    return (np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes()


def wav(name: str) -> bytes:
    with wave.open(str(FIXTURES / name)) as w:
        return w.readframes(w.getnframes())


rng = np.random.default_rng(0)
t = np.arange(SR * 2) / SR
NOISE = {
    "silence": np.zeros(SR * 2),
    "white noise": rng.normal(0, 0.1, SR * 2),
    "50 Hz hum": 0.3 * np.sin(2 * np.pi * 50 * t),
    "1 kHz tone": 0.3 * np.sin(2 * np.pi * 1000 * t),
    "keyboard clicks": np.where((np.arange(SR * 2) % 1600) < 40, 0.8, 0.0) * rng.normal(0, 1, SR * 2),
    "loud fan": rng.normal(0, 0.4, SR * 2) * (0.8 + 0.2 * np.sin(2 * np.pi * 3 * t)),
}


@pytest.mark.parametrize("name", NOISE)
def test_noise_has_no_speech(name):
    assert voiced_ms(pcm(NOISE[name])) < 250


def test_a_sentence_is_speech():
    assert voiced_ms(wav("speech_weather.wav")) > 1200


def test_a_one_word_command_is_speech():
    assert voiced_ms(wav("speech_stop.wav")) >= 250


def test_resampling_and_empty_input():
    speech = wav("speech_weather.wav")
    as_48k = np.repeat(np.frombuffer(speech, dtype=np.int16), 3).tobytes()
    assert voiced_ms(as_48k, rate=48000) > 1200
    assert voiced_ms(b"") == 0
