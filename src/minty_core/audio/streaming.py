"""Streaming speech recognition for word-by-word captions (sherpa-onnx, optional).

The recogniser is fed 80 ms frames and returns the text so far, so words appear while you speak. It is only
for the display and for early intent: the final text that Minty acts on still comes from the normal
recogniser (Groq or local Whisper). Any failure turns it off for the session and the chunked-Whisper captions
take over.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


def _pick(folder: Path, stem: str) -> str | None:
    found = sorted(folder.glob(f"{stem}*.onnx"), key=lambda p: ("int8" not in p.name, p.name))
    return str(found[0]) if found else None


class SherpaStreaming:
    def __init__(self, model_dir: Path, threads: int = 2, rate: int = 16000):
        import sherpa_onnx  # optional dependency; absence is handled by build_streaming

        folder = Path(model_dir)
        files = {k: _pick(folder, k) for k in ("encoder", "decoder", "joiner")}
        tokens = folder / "tokens.txt"
        if not all(files.values()) or not tokens.is_file():
            raise FileNotFoundError(f"streaming model files missing in {folder}")
        self._rate = rate
        self._rec = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(tokens), encoder=files["encoder"], decoder=files["decoder"], joiner=files["joiner"],
            num_threads=threads, sample_rate=rate, feature_dim=80, decoding_method="greedy_search")

    def new_stream(self):
        return self._rec.create_stream()

    def accept(self, stream, pcm: bytes) -> str:
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        stream.accept_waveform(self._rate, samples)
        while self._rec.is_ready(stream):
            self._rec.decode_stream(stream)
        result = self._rec.get_result(stream)
        return str(getattr(result, "text", result) or "").strip()


def build_streaming(cfg, data_dir: Path):
    """The streaming recogniser if it is switched on and usable, else None (never raises)."""
    if getattr(cfg, "streaming_asr", "off") != "sherpa":
        return None
    folder = Path(cfg.streaming_model_dir) if cfg.streaming_model_dir else data_dir / "sherpa-streaming"
    try:
        return SherpaStreaming(folder)
    except ImportError:
        log.warning("streaming captions are on but sherpa-onnx is not installed (pip install sherpa-onnx)")
    except Exception as e:  # noqa: BLE001
        log.warning("streaming captions unavailable: %s", e)
    return None
