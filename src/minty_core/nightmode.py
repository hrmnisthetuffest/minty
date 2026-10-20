"""Night behaviour: quieter speech and a less eager wake detector inside the quiet hours.

Proactive pushes are already held at night by `proactive.night_from/night_to`; this adds what the speaker and the
microphone do. Hours cross midnight ("20:30" to "06:00") correctly.
"""
from __future__ import annotations

from datetime import datetime, time

import numpy as np


def _hhmm(text: str) -> time:
    hour, _, minute = str(text).partition(":")
    return time(int(hour), int(minute or 0))


def in_quiet_hours(now: datetime, start: str, end: str) -> bool:
    t, s, e = now.time().replace(second=0, microsecond=0), _hhmm(start), _hhmm(end)
    return s <= t or t < e if s > e else s <= t < e


def voice_gain(now: datetime, ops) -> float:
    """1.0 by day, ops.quiet_voice_gain inside the quiet hours."""
    return float(ops.quiet_voice_gain) if in_quiet_hours(now, ops.quiet_voice_from, ops.quiet_voice_to) else 1.0


def wake_boost(now: datetime, ops) -> float:
    """Added to the model wake threshold at night, so a cough or the television is less likely to wake it."""
    return float(ops.night_wake_boost) if in_quiet_hours(now, ops.quiet_voice_from, ops.quiet_voice_to) else 0.0


def scale_pcm(pcm: bytes, gain: float) -> bytes:
    """16-bit mono PCM scaled by `gain`, clipped, never wrapped."""
    if abs(gain - 1.0) < 0.001 or not pcm:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) * max(0.0, gain)
    return np.clip(samples, -32768, 32767).astype(np.int16).tobytes()
