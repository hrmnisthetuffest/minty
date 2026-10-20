#!/usr/bin/env python3
"""Offline benchmark of speech capture: the real endpointer and speech detector on recorded speech mixed with
room noise at different levels. No microphone is used.

  .venv/bin/python scripts/voice_bench.py            # compare loudness-only with the speech-detector assist
Reports, per scenario: was speech detected, how long after it started, was the sentence cut early, how long after
it ended the capture closed, and whether noise alone was taken for speech.
"""
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from minty_core.audio.endpointer import EnergyEndpointer  # noqa: E402
from minty_core.audio.types import FRAME_SAMPLES  # noqa: E402
from minty_core.audio.vad import FrameVAD  # noqa: E402
from minty_core.config import AudioConfig  # noqa: E402

RATE = 16000


def load(name: str) -> np.ndarray:
    with wave.open(str(ROOT / "tests" / "fixtures" / name)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def at_db(x: np.ndarray, db: float) -> np.ndarray:
    rms = np.sqrt(np.mean(x ** 2)) or 1e-9
    return x * (10 ** (db / 20) / rms)


def noise(seconds: float, db: float, kind: str, rng) -> np.ndarray:
    n = int(seconds * RATE)
    x = rng.normal(0, 1, n) if kind == "hiss" else np.cumsum(rng.normal(0, 1, n))
    if kind != "hiss":
        x = x - np.convolve(x, np.ones(400) / 400, mode="same")  # keep the low rumble, drop the drift
    return at_db(x.astype(np.float32), db)


def scenario(speech_db, room_db=-46.0, pause=False, speech=True, noise_kind="fan", seed=0):
    rng = np.random.default_rng(seed)
    voice = load("speech_weather.wav")
    if pause:  # "Minty, what is the weather ... (0.7 s) ... like today?"; pause may be a length in seconds
        cut = len(voice) // 2
        seconds = 0.7 if pause is True else float(pause)
        voice = np.concatenate([voice[:cut], np.zeros(int(seconds * RATE), np.float32), voice[cut:]])
    lead, tail = int(1.5 * RATE), int(2.0 * RATE)
    total = lead + len(voice) + tail
    room = noise(total / RATE, room_db, noise_kind, rng)
    mix = room.copy()
    if speech:
        mix[lead:lead + len(voice)] += at_db(voice, speech_db) if speech_db is not None else 0
    pcm = (np.clip(mix, -1, 1) * 32767).astype(np.int16)
    return pcm, lead, lead + len(voice)


def run(pcm, start, end, assist: bool):
    cfg = AudioConfig(vad_assist=assist)
    ep = EnergyEndpointer(lambda: cfg, vad=FrameVAD() if assist else None)
    detected_at = closed_at = None
    for i in range(0, len(pcm) - FRAME_SAMPLES, FRAME_SAMPLES):
        done = ep.feed(pcm[i:i + FRAME_SAMPLES].tobytes())
        if detected_at is None and ep.heard_speech:
            detected_at = i + FRAME_SAMPLES
        if done and ep.heard_speech:
            closed_at = i + FRAME_SAMPLES
            break
        if done:
            ep.reset()  # nothing heard yet: keep waiting, as the pipeline does between attempts
    return detected_at, closed_at


def main() -> int:
    rows = [("normal voice -26 dB", dict(speech_db=-26)), ("quiet voice -40 dB", dict(speech_db=-40)),
            ("very quiet voice -45 dB", dict(speech_db=-45)), ("voice with a 0.7 s pause", dict(speech_db=-34,
                                                                                                pause=True)),
            ("loud fan only -40 dB", dict(speech_db=None, speech=False, room_db=-40)),
            ("hiss only -44 dB", dict(speech_db=None, speech=False, room_db=-44, noise_kind="hiss"))]
    print(f"{'scenario':26} {'mode':10} {'detected':>9} {'start ms':>9} {'cut early':>9} {'close ms':>9}")
    for name, kw in rows:
        pcm, start, end = scenario(**kw)
        for assist in (False, True):
            det, closed = run(pcm, start, end, assist)
            speech = kw.get("speech", True)
            if speech:
                cut = closed is not None and closed < end
                print(f"{name:26} {'assist' if assist else 'loudness':10} {str(det is not None):>9} "
                      f"{'-' if det is None else round((det - start) / RATE * 1000):>9} {str(cut):>9} "
                      f"{'-' if closed is None else round((closed - end) / RATE * 1000):>9}")
            else:
                print(f"{name:26} {'assist' if assist else 'loudness':10} {'FALSE ALARM' if det else 'ignored':>9}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
