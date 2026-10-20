"""Voice-note audio: Telegram's Ogg/Opus <-> 16 kHz mono PCM, with PyAV (which ships with faster-whisper).

"Opus" is the audio codec Telegram voice messages use, nothing to do with a language model.
"""
from __future__ import annotations

import io

STT_RATE = 16000
OPUS_RATE = 48000


class CodecUnavailable(RuntimeError):
    """The audio could not be decoded or encoded here (not audio, or PyAV has no Opus)."""


def _av():
    try:
        import av
    except ImportError as e:  # pragma: no cover — PyAV comes with faster-whisper
        raise CodecUnavailable("audio conversion needs PyAV (installed with faster-whisper)") from e
    return av


def ogg_to_pcm(data: bytes, rate: int = STT_RATE) -> bytes:
    """16-bit mono PCM at `rate` from an Ogg/Opus (or any audio) file."""
    av = _av()
    try:
        with av.open(io.BytesIO(data)) as container:
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is None:
                raise CodecUnavailable("that file has no audio in it")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
            out = bytearray()
            for frame in container.decode(stream):
                for piece in resampler.resample(frame):
                    out += piece.to_ndarray().tobytes()
            for piece in resampler.resample(None):
                out += piece.to_ndarray().tobytes()
            return bytes(out)
    except CodecUnavailable:
        raise
    except Exception as e:  # noqa: BLE001 — av raises many types for a file it cannot read
        raise CodecUnavailable(f"I could not read that audio ({type(e).__name__})") from None


def pcm_to_ogg(pcm: bytes, rate: int) -> bytes:
    """An Ogg/Opus voice file from 16-bit mono PCM."""
    av = _av()
    import numpy as np

    try:
        buffer = io.BytesIO()
        with av.open(buffer, "w", format="ogg") as container:
            stream = container.add_stream("libopus", rate=OPUS_RATE)
            stream.layout = "mono"
            resampler = av.AudioResampler(format="s16", layout="mono", rate=OPUS_RATE)
            samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
            frame.sample_rate = rate
            for piece in resampler.resample(frame) + resampler.resample(None):
                for packet in stream.encode(piece):
                    container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)
        return buffer.getvalue()
    except Exception as e:  # noqa: BLE001
        raise CodecUnavailable(f"I could not make a voice file ({type(e).__name__}: {e})") from None
