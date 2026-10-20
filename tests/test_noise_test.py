import wave
from pathlib import Path

import numpy as np

from minty_core.audio.types import Transcription
from minty_core.audio.vad import voiced_ms
from minty_core.config import AudioConfig
from minty_core.noisetest import analyse

FIX = Path(__file__).parent / "fixtures"


def frames(pcm, size=2560):
    return [pcm[i:i + size] for i in range(0, len(pcm) - size + 1, size)]


def test_noise_bursts_are_dropped_and_speech_is_kept():
    rng = np.random.default_rng(1)
    quiet = (rng.normal(0, 50, 16000)).astype(np.int16).tobytes()
    bang = (rng.normal(0, 6000, 8000)).astype(np.int16).tobytes()  # half a second of loud noise
    with wave.open(str(FIX / "speech_weather.wav")) as w:
        speech = w.readframes(w.getnframes())
    pcm = quiet + bang + quiet + speech + quiet
    calls = []

    def transcribe(seg):
        calls.append(len(seg))
        return Transcription("what is the weather like today")

    result = analyse(frames(pcm), AudioConfig(), voiced_ms, transcribe)
    assert result["segments"] >= 2 and len(result["accepted"]) == 1
    assert result["accepted"][0]["text"] == "what is the weather like today"
    assert sum(result["dropped"].values()) >= 1 and len(calls) == 1  # the bang was never transcribed
    assert result["noise_floor"] is not None
