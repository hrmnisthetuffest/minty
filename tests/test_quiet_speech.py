"""Quiet speech is captured; noise at the same loudness is not (the speech-detector assist)."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("voice_bench", Path(__file__).parents[1] / "scripts" / "voice_bench.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


@pytest.mark.parametrize("kw, assist_detects, loudness_detects", [
    (dict(speech_db=-26), True, True),
    (dict(speech_db=-42), True, False),  # below the fixed loudness threshold: only the assist hears it
    (dict(speech_db=-34, pause=True), True, True),
])
def test_speech_is_caught_and_never_cut_early(kw, assist_detects, loudness_detects):
    pcm, start, end = bench.scenario(**kw)
    for assist, expected in ((True, assist_detects), (False, loudness_detects)):
        detected, closed = bench.run(pcm, start, end, assist)
        assert (detected is not None) is expected
        if detected is not None:
            assert detected - start <= 0.5 * bench.RATE  # noticed within half a second
            assert closed is None or closed >= end  # a pause inside the sentence does not end it


@pytest.mark.parametrize("kw", [dict(speech=False, speech_db=None, room_db=-40),
                                dict(speech=False, speech_db=None, room_db=-44, noise_kind="hiss")])
def test_noise_alone_is_never_taken_for_speech(kw):
    pcm, start, end = bench.scenario(**kw)
    assert bench.run(pcm, start, end, True)[0] is None
    assert bench.run(pcm, start, end, True, auto=True)[0] is None  # nor with the bar that follows the room
    assert bench.run(pcm, start, end, False, auto=True)[0] is None


def test_the_assist_can_be_switched_off_and_is_skipped_without_a_detector():
    from minty_core.audio.vad import is_voice
    assert is_voice(500, 400, 100, b"\0\0" * 1280, None) is True
    assert is_voice(300, 400, 100, b"\0\0" * 1280, None) is False  # no detector: loudness alone, as before


def test_a_quiet_minty_is_not_clipped_off_the_start_of_the_utterance():
    """Loudness alone starts the utterance late when the voice is quiet, which drops the wake word itself."""
    from minty_core.audio.types import FRAME_SAMPLES
    from minty_core.audio.vad import FrameVAD
    from minty_core.audio.wakewords import TranscriptWakeDetector
    from minty_core.config import AudioConfig

    pcm, start, end = bench.scenario(speech_db=-40)
    speech_s = (end - start) / bench.RATE
    lengths = {}
    for assist in (False, True):
        cfg = AudioConfig(vad_assist=assist)
        segments = []
        det = TranscriptWakeDetector(lambda audio: segments.append(len(audio) / 2 / bench.RATE) or "",
                                     lambda: cfg, vad=FrameVAD() if assist else None)
        for i in range(0, len(pcm) - FRAME_SAMPLES, FRAME_SAMPLES):
            det.detect(pcm[i:i + FRAME_SAMPLES].tobytes())
        lengths[assist] = max(segments, default=0.0)
    assert lengths[True] >= speech_s  # the whole sentence, "Minty" included, reaches the recogniser
    assert lengths[False] < lengths[True]  # without the assist the start was lost (the clip is synthetic: a short loss)
