"""Earcons: tiny generated tones, so "I'm listening" can be a chime instead of words (D-7). Deterministic, quiet
(peak 0.25 of full scale), with a 10 ms fade at both ends so they never click."""
from __future__ import annotations

import numpy as np

from minty_core.audio.types import Audio

RATE = 24000
PEAK = 0.25
FADE_S = 0.010
# kind -> [(Hz, seconds), ...]
KINDS = {
    "wake": [(660.0, 0.09), (880.0, 0.11)],
    "done": [(523.0, 0.14)],
    "error": [(440.0, 0.12), (330.0, 0.16)],
    "approve": [(784.0, 0.07), (988.0, 0.09)],
}


def tone(kind: str, rate: int = RATE) -> Audio:
    notes = KINDS[kind]
    parts = []
    for hz, seconds in notes:
        t = np.arange(int(rate * seconds)) / rate
        wave = np.sin(2 * np.pi * hz * t)
        fade = min(len(t) // 2, int(rate * FADE_S))
        env = np.ones(len(t))
        env[:fade] = np.linspace(0, 1, fade)
        env[-fade:] = np.linspace(1, 0, fade)
        parts.append(wave * env * PEAK)
    pcm = (np.concatenate(parts) * 32767).astype(np.int16)
    return Audio(pcm.tobytes(), rate)
