"""The speech threshold follows the room and the microphone: it is learned from the background level, falls quickly when the room
gets quieter (music turned down) and rises slowly, and a sensitivity setting scales it for a mic that is hotter or deafer than most."""
import numpy as np
import pytest

from minty_core.audio.endpointer import EnergyEndpointer
from minty_core.audio.sensitivity import speech_threshold, track_floor
from minty_core.audio.wakewords import TranscriptWakeDetector
from minty_core.config import AudioConfig


def frame(amplitude, n=1280):
    return np.full(n, amplitude, dtype=np.int16).tobytes()


def cfg(**kw):
    return AudioConfig(**kw)


def test_before_the_room_is_known_the_configured_level_is_used():
    assert speech_threshold(cfg(), None) == 400
    assert speech_threshold(cfg(speech_rms_threshold=900), None) == 900


@pytest.mark.parametrize("floor, expected", [(20, 100), (100, 250), (1000, 2500)])
def test_auto_follows_the_room_with_a_floor_of_a_quarter_of_the_configured_level(floor, expected):
    assert speech_threshold(cfg(), floor) == pytest.approx(expected)  # max(400 / 4, floor x 2.5)


@pytest.mark.parametrize("floor, expected", [(20, 400), (100, 400), (1000, 2500)])
def test_with_auto_off_the_configured_level_is_a_fixed_minimum(floor, expected):
    assert speech_threshold(cfg(auto_sensitivity=False), floor) == pytest.approx(expected)


def test_a_room_factor_of_zero_means_the_level_is_fixed():
    assert speech_threshold(cfg(noise_floor_factor=0), 2000) == 400


def test_sensitivity_scales_the_bar_but_never_below_the_room_itself():
    assert speech_threshold(cfg(mic_sensitivity=2.0), 1000) == pytest.approx(1250)
    assert speech_threshold(cfg(mic_sensitivity=0.5), 1000) == pytest.approx(5000)
    assert speech_threshold(cfg(mic_sensitivity=4.0), 100) >= 100 * 1.2  # background is never speech


def test_the_floor_falls_fast_and_rises_slowly():
    assert track_floor(None, 300) == 300
    fell = track_floor(1000, 100)
    rose = track_floor(100, 1000)
    assert fell < 800 and rose < 200  # 1000 -> under 800 in one frame; 100 -> under 200 in one frame


def test_the_threshold_recovers_within_a_second_when_the_music_is_turned_down():
    e = EnergyEndpointer(lambda: cfg(min_speech_frames=1, max_wait_ms=60_000))
    e.reset()
    for _ in range(60):  # a music bed just under the speech bar
        e.feed(frame(380))
    high = e.threshold
    for _ in range(12):  # about one second of the music ducked to a murmur (12.5 frames a second)
        e.feed(frame(60))
    assert high > 900 and e.threshold < high * 0.35


def test_a_quiet_microphone_is_still_heard_in_a_quiet_room():
    quiet = lambda: cfg(min_speech_frames=1, max_wait_ms=60_000)  # noqa: E731
    e = EnergyEndpointer(quiet)
    e.reset()
    for _ in range(20):
        e.feed(frame(12))
    e.feed(frame(220))  # a quiet mic: the loudest speech it hears is about 220
    assert e.heard_speech is True
    fixed = EnergyEndpointer(lambda: cfg(min_speech_frames=1, max_wait_ms=60_000, auto_sensitivity=False))
    fixed.reset()
    for _ in range(20):
        fixed.feed(frame(12))
    fixed.feed(frame(220))
    assert fixed.heard_speech is False  # the old fixed bar of 400 missed it


def test_a_hot_microphone_in_a_noisy_room_does_not_hear_the_room_as_speech():
    e = EnergyEndpointer(lambda: cfg(min_speech_frames=1, max_wait_ms=60_000))
    e.reset()
    for _ in range(40):
        e.feed(frame(350))  # a fan the microphone picks up loudly: the room's level is learned
    for _ in range(10):
        e.feed(frame(800))  # and swells a little: still the room
    assert e.heard_speech is False
    e.feed(frame(4000))
    assert e.heard_speech is True


def test_the_wake_detector_uses_the_same_bar():
    heard = []
    det = TranscriptWakeDetector(lambda pcm: heard.append(1) or "", lambda: cfg())
    for _ in range(20):
        det.detect(frame(12))
    det.detect(frame(220))
    assert det._buf  # 220 is above the learned bar for this room, so the utterance is being collected
    det2 = TranscriptWakeDetector(lambda pcm: "", lambda: cfg(auto_sensitivity=False))
    for _ in range(20):
        det2.detect(frame(12))
    det2.detect(frame(220))
    assert not det2._buf


def test_the_settings_have_sane_limits():
    with pytest.raises(ValueError):
        cfg(mic_sensitivity=0.0)
    with pytest.raises(ValueError):
        cfg(mic_sensitivity=20)
    assert cfg().auto_sensitivity is True and cfg().mic_sensitivity == 1.0
