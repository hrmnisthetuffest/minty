"""Quieter speech and a calmer wake detector at night (W1B-05)."""
from datetime import datetime

import numpy as np

from minty_core.audio.devices import OpenWakeWordDetector
from minty_core.config import OpsConfig
from minty_core.nightmode import in_quiet_hours, scale_pcm, voice_gain, wake_boost


def test_quiet_hours_cross_midnight():
    at = lambda h, m=0: datetime(2026, 10, 14, h, m)  # noqa: E731
    assert in_quiet_hours(at(20, 30), "20:30", "06:00") and in_quiet_hours(at(23), "20:30", "06:00")
    assert in_quiet_hours(at(2), "20:30", "06:00") and in_quiet_hours(at(5, 59), "20:30", "06:00")
    assert not in_quiet_hours(at(6), "20:30", "06:00") and not in_quiet_hours(at(20, 29), "20:30", "06:00")
    assert in_quiet_hours(at(13), "12:00", "14:00") and not in_quiet_hours(at(15), "12:00", "14:00")


def test_gain_and_boost_only_apply_inside_the_hours():
    ops = OpsConfig()
    assert ops.quiet_voice_from == "20:30" and ops.quiet_voice_gain == 0.5
    assert voice_gain(datetime(2026, 10, 14, 21, 0), ops) == 0.5 and voice_gain(datetime(2026, 10, 14, 15, 0), ops) == 1.0
    assert wake_boost(datetime(2026, 10, 14, 22, 0), ops) > 0 and wake_boost(datetime(2026, 10, 14, 9, 0), ops) == 0


def test_scaling_halves_the_samples_and_never_wraps():
    loud = np.array([20000, -20000, 32767, -32768], dtype=np.int16).tobytes()
    assert np.frombuffer(scale_pcm(loud, 0.5), dtype=np.int16).tolist() == [10000, -10000, 16383, -16384]
    assert np.frombuffer(scale_pcm(loud, 2.0), dtype=np.int16).tolist() == [32767, -32768, 32767, -32768]
    assert scale_pcm(loud, 1.0) is loud and scale_pcm(b"", 0.5) == b""


def test_the_model_detector_needs_more_confidence_at_night():
    class Model:
        def predict(self, frame):
            return {"minty": 0.55}

    boost = {"v": 0.0}
    det = OpenWakeWordDetector("minty", 0.5, model_factory=lambda w: Model(), boost=lambda: boost["v"])
    frame = np.zeros(1280, dtype=np.int16).tobytes()
    assert det.detect(frame) is True
    boost["v"] = 0.15
    assert det.detect(frame) is False
