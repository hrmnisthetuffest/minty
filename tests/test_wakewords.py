import numpy as np
import pytest

from minty_core.audio.wakewords import TranscriptWakeDetector, match_wake
from minty_core.config import AudioConfig

PHRASES = ["minty", "mintee", "minti", "mindy"]


@pytest.mark.parametrize("text, rest", [
    ("Minty", ""),
    ("Minty.", ""),
    ("mintee, what's the weather?", "what's the weather?"),
    ("Hey Minty what time is it", "what time is it"),
    ("okay Mintee turn on the light", "turn on the light"),
    ("Mindy, stop", "stop"),
    ("MINTY!", ""),
])
def test_wake_phrases_match(text, rest):
    assert match_wake(text, PHRASES) == rest


@pytest.mark.parametrize("text", [
    "", "what is the time", "extra cheese please",
    "tell minty I said hi", "ultimate frisbee", "alter the plan",
    "mint chocolate chip please", "mints are on the table", "mini golf tomorrow", "monty python is on",
])
def test_other_speech_does_not_match(text):
    assert match_wake(text, PHRASES) is None


def frame(level):
    return (np.full(1280, level, dtype=np.int16)).tobytes()


def speak(det, n_loud, n_quiet=12):
    hits = [det.detect(frame(3000)) for _ in range(n_loud)]
    hits += [det.detect(frame(0)) for _ in range(n_quiet)]
    return any(hits)


def test_detector_wakes_after_utterance_and_exposes_remainder():
    seen = []
    det = TranscriptWakeDetector(lambda pcm: seen.append(len(pcm)) or "Minty, what time is it", AudioConfig)
    assert speak(det, 10) is True
    assert det.remainder == "what time is it"
    assert len(seen) == 1


def test_detector_ignores_unrelated_speech_and_clicks():
    texts = iter(["what is the time"])
    det = TranscriptWakeDetector(lambda pcm: next(texts), AudioConfig)
    assert speak(det, 10) is False and det.remainder == ""
    assert speak(det, 1) is False  # a click shorter than ~0.24s is never transcribed


def test_transcriber_failure_is_not_a_wake():
    def boom(pcm):
        raise RuntimeError("model missing")

    det = TranscriptWakeDetector(boom, AudioConfig)
    assert speak(det, 10) is False


def test_detector_streams_partial_captions_while_speaking():
    partials = []
    det = TranscriptWakeDetector(lambda pcm: "minty what", AudioConfig, on_partial=partials.append)
    assert speak(det, 30) is True
    assert partials and set(partials) == {"minty what"}


def test_ten_seconds_without_a_pause_is_never_transcribed():
    calls = []
    det = TranscriptWakeDetector(lambda pcm: calls.append(1) or "minty do something", AudioConfig)
    assert speak(det, 200) is False
    assert calls == []


def test_noise_is_never_transcribed_by_the_wake_detector():
    transcribed = []
    det = TranscriptWakeDetector(lambda pcm: transcribed.append(pcm) or "Minty, what time is it", AudioConfig,
                                 speech_check=lambda pcm: 0)
    assert not speak(det, 8) and transcribed == []
    det = TranscriptWakeDetector(lambda pcm: "Minty, what time is it", AudioConfig, speech_check=lambda pcm: 900)
    assert speak(det, 8)
