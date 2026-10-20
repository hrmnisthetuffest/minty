from typing import Callable

import numpy as np

from minty_core.audio.sensitivity import speech_threshold, track_floor
from minty_core.audio.types import FRAME_S
from minty_core.audio.vad import is_voice
from minty_core.config import AudioConfig

FRAME_MS = round(FRAME_S * 1000)


def rms(frame: bytes) -> float:
    samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(samples ** 2))) if samples.size else 0.0


def _frames(ms: int) -> int:
    return max(1, -(-ms // FRAME_MS))


class EnergyEndpointer:
    """Decides when an utterance has ended, from loudness.

    - speech only counts after `min_speech_frames` loud frames in a row (a door knock or a cough is a blip);
    - the threshold follows the room and the microphone (audio/sensitivity.py): it is `noise_floor_factor` × the measured
      background level, so a fan or music bed does not keep the recording open, and it comes back down quickly when the room
      gets quieter. The background estimate survives resets.
    """

    def __init__(self, get_cfg: Callable[[], AudioConfig], vad=None):
        self._get_cfg = get_cfg
        self._vad = vad  # FrameVAD or None: lets quiet speech count (see audio.vad.is_voice)
        self.heard_speech = False
        self.noise_floor: float | None = None
        self.reset()

    def reset(self) -> None:
        cfg = self._get_cfg()
        self._silence_frames = _frames(cfg.silence_ms)
        self._wait_frames = _frames(cfg.max_wait_ms)
        self._min_loud = cfg.min_speech_frames
        self.heard_speech = False
        self._quiet = 0
        self._waited = 0
        self._loud_run = 0

    @property
    def quiet_ms(self) -> int:
        """How long it has been quiet since the last speech; 0 while speaking and before any speech."""
        return self._quiet * FRAME_MS if self.heard_speech else 0

    @property
    def threshold(self) -> float:
        return speech_threshold(self._get_cfg(), self.noise_floor)

    def feed(self, frame: bytes) -> bool:
        level = rms(frame)
        vad = self._vad if self._get_cfg().vad_assist else None
        if is_voice(level, self.threshold, self.noise_floor, frame, vad):
            self._loud_run += 1
            if self._loud_run >= self._min_loud:
                self.heard_speech = True
            self._quiet = 0
            return False
        if not self.heard_speech:  # background: learn the room's level
            self.noise_floor = track_floor(self.noise_floor, level)
        self._loud_run = 0
        if self.heard_speech:
            self._quiet += 1
            return self._quiet >= self._silence_frames
        self._waited += 1
        return self._waited >= self._wait_frames
