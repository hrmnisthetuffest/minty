"""The voice benchmark pack (697): quiet speech, noise and pauses through the real endpointer and speech detector.
This file and tests/test_quiet_speech.py are the pack; it must stay green whenever audio code changes."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("voice_bench", Path(__file__).parents[1] / "scripts" / "voice_bench.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


@pytest.mark.parametrize("pause_s", [0.3, 0.5, 0.8])
def test_a_pause_shorter_than_the_silence_window_never_ends_the_sentence(pause_s):
    pcm, start, end = bench.scenario(speech_db=-30, pause=pause_s)
    detected, closed = bench.run(pcm, start, end, True)
    assert detected is not None and (closed is None or closed >= end)


def test_a_long_pause_does_end_it_which_is_what_the_turn_hold_is_for():
    pcm, start, end = bench.scenario(speech_db=-30, pause=1.4)
    detected, closed = bench.run(pcm, start, end, True)
    assert detected is not None and closed is not None and closed < end  # cut in the middle: the known limit


@pytest.mark.parametrize("db", [-26, -34, -40])
def test_quiet_and_normal_speech_is_always_heard_within_half_a_second(db):
    pcm, start, end = bench.scenario(speech_db=db)
    detected, closed = bench.run(pcm, start, end, True)
    assert detected is not None and detected - start <= 0.5 * bench.RATE
    assert closed is not None and closed - end <= 1.5 * bench.RATE  # closes within 1.5 s of the last word


@pytest.mark.parametrize("room_db, kind", [(-40, "fan"), (-44, "hiss"), (-38, "fan")])
def test_room_noise_alone_is_never_speech(room_db, kind):
    pcm, start, end = bench.scenario(speech=False, speech_db=None, room_db=room_db, noise_kind=kind)
    assert bench.run(pcm, start, end, True)[0] is None
