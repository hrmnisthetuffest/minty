"""Is there any speech in this recording? Silero VAD, which ships inside faster-whisper (no extra package).

Used before speech-to-text: a fan, music bass, a door or a cough has ~0 ms of speech and never reaches Whisper,
which would otherwise "hear" words in it.
"""
import logging

import numpy as np

from minty_core.audio.types import SAMPLE_RATE

log = logging.getLogger(__name__)


def voiced_ms(pcm: bytes, rate: int = SAMPLE_RATE) -> int | None:
    """Milliseconds of speech in 16-bit mono PCM, or None when no detector is available (then: let it through)."""
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps
    except ImportError:
        return None
    try:
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if rate != SAMPLE_RATE and audio.size:
            positions = np.linspace(0, audio.size - 1, int(audio.size * SAMPLE_RATE / rate))
            audio = np.interp(positions, np.arange(audio.size), audio).astype(np.float32)
        spans = get_speech_timestamps(audio, VadOptions(speech_pad_ms=0, min_silence_duration_ms=100),
                                      sampling_rate=SAMPLE_RATE)
        return int(sum(s["end"] - s["start"] for s in spans) * 1000 / SAMPLE_RATE)
    except Exception:
        log.warning("speech detector failed; letting the audio through", exc_info=True)
        return None


class FrameVAD:
    """Speech probability of the newest 80 ms frame (Silero, with a little earlier audio as context).

    Used for frames that are louder than the room but quieter than the speech threshold: quiet or distant
    speech passes, a fan or hiss at the same loudness does not. Loud frames and frames at the room's own
    level never get here, so it costs nothing most of the time (about 0.5 ms when it runs).
    """

    def __init__(self, context_frames: int = 2):
        from collections import deque

        from faster_whisper.vad import get_vad_model  # ships with faster-whisper; ImportError -> caller skips

        self._model = get_vad_model()
        self._context: deque = deque(maxlen=context_frames)

    def __call__(self, frame: bytes) -> float:
        self._context.append(frame)
        audio = np.frombuffer(b"".join(self._context), dtype=np.int16).astype(np.float32) / 32768.0
        usable = (audio.size // 512) * 512
        if usable == 0:
            return 0.0
        probs = np.asarray(self._model(audio[-usable:])).reshape(-1)
        newest = max(1, int(np.ceil(len(frame) / 2 / 512)))  # the chunks that cover the newest frame
        return float(probs[-newest:].max())

    def remember(self, frame: bytes) -> None:
        """Keep context without scoring (for frames that were decided by loudness alone)."""
        self._context.append(frame)


def build_frame_vad():
    try:
        return FrameVAD()
    except Exception:  # noqa: BLE001 — no onnxruntime/model: the loudness gate works alone, as before
        log.warning("speech detector unavailable; quiet speech relies on loudness alone", exc_info=True)
        return None


def is_voice(level: float, threshold: float, floor: float | None, frame: bytes, vad, min_prob: float = 0.5) -> bool:
    """Loud enough, or clearly above the room and sounding like a voice."""
    if level >= threshold:
        if vad is not None:
            vad.remember(frame)
        return True
    lower = max(QUIET_SPEECH_RMS, (floor or 0.0) * FLOOR_MARGIN)
    if vad is None or level < lower:
        if vad is not None:
            vad.remember(frame)
        return False
    return vad(frame) >= min_prob


QUIET_SPEECH_RMS = 60.0  # about -55 dBFS: below this nothing is treated as speech, however it sounds
FLOOR_MARGIN = 1.15  # a frame must be a little louder than the room before the speech detector is asked
