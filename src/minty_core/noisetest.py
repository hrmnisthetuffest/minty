"""Calibration: how often would this room's noise turn into words? Used by scripts/noise_test.py."""
from collections import Counter
from typing import Callable, Iterable

from minty_core.audio.endpointer import EnergyEndpointer
from minty_core.audio.gate import judge
from minty_core.audio.types import Transcription
from minty_core.config import AudioConfig


def segments(frames: Iterable[bytes], cfg: AudioConfig) -> tuple[list[bytes], float | None]:
    """Cut a recording into utterances exactly as the pipeline would."""
    ep = EnergyEndpointer(lambda: cfg)
    ep.reset()
    out, buf = [], []
    for frame in frames:
        buf.append(frame)
        if ep.feed(frame):
            if ep.heard_speech:
                out.append(b"".join(buf))
            buf = []
            ep.reset()
    if ep.heard_speech and buf:
        out.append(b"".join(buf))
    return out, ep.noise_floor


def analyse(frames: Iterable[bytes], cfg: AudioConfig, speech_check: Callable[[bytes], int | None],
            transcribe: Callable[[bytes], Transcription]) -> dict:
    parts, floor = segments(frames, cfg)
    rows = []
    for pcm in parts:
        voiced = speech_check(pcm)
        if voiced is not None and voiced < cfg.min_voiced_ms:
            rows.append({"verdict": "dropped", "reason": f"only {voiced} ms of speech", "voiced_ms": voiced, "text": ""})
            continue
        heard = transcribe(pcm)
        verdict = judge(heard.text, no_speech_prob=heard.no_speech_prob, avg_logprob=heard.avg_logprob,
                        voiced_ms=voiced, min_voiced_ms=cfg.min_voiced_ms)
        rows.append({"verdict": "accepted" if verdict.accept else "dropped", "reason": verdict.reason,
                     "voiced_ms": voiced, "text": heard.text})
    return {"segments": len(parts), "accepted": [r for r in rows if r["verdict"] == "accepted"],
            "dropped": Counter(r["reason"].split(" ms")[0] if "ms of speech" in r["reason"] else r["reason"]
                               for r in rows if r["verdict"] == "dropped"),
            "rows": rows, "noise_floor": floor}
