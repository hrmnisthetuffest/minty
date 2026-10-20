"""Wake word by speech recognition: transcribe each short utterance locally and look for "Minty".

openWakeWord only ships a few fixed phrases, so this matches the name itself (and common
mis-hearings such as "mintee") in the first words of an utterance. Audio never leaves the machine.
"""
import difflib
import logging
import re
import time
from collections import deque
from typing import Callable

import numpy as np

from minty_core.audio.endpointer import rms
from minty_core.audio.sensitivity import speech_threshold, track_floor
from minty_core.audio.types import FRAME_S
from minty_core.audio.vad import is_voice
from minty_core.config import AudioConfig

log = logging.getLogger(__name__)

_PREFIXES = {"hey", "hi", "ok", "okay", "yo", "hello", "oh"}
_REAL_WORDS = {"mint", "mints", "mind", "mine", "mini", "minted"}  # close to the name and never meant for it
_WORD = re.compile(r"[A-Za-z0-9']+")
_SILENCE_FRAMES = max(1, round(0.6 / FRAME_S))
_MAX_FRAMES = round(10 / FRAME_S)
_MIN_SPEECH_FRAMES = 3
_PRE_ROLL = 3
_MUSIC_MAX_WORDS = 10
_MUSIC_MAX_FRAMES = round(6 / FRAME_S)
_MUSIC_MIN_GAP_S = 1.5
_CAPTION_EVERY = round(1.0 / FRAME_S)
_CAPTION_MAX_FRAMES = round(4 / FRAME_S)  # only caption the start; long monologues are rarely for us


def match_wake(text: str, phrases, strict: bool = False) -> str | None:
    """Return what follows the wake word (possibly ""), or None when it is not addressed to us.

    strict (music is playing): exact names only, a short utterance, no fuzzy matching. Lyrics are long
    and rarely start with the name."""
    wanted = {p.lower() for p in phrases}
    name = (list(phrases) or ["minty"])[0].lower()  # the first phrase is the name; the rest are mis-hearings
    if strict and len(_WORD.findall(text)) > _MUSIC_MAX_WORDS:
        return None
    for position, m in enumerate(_WORD.finditer(text)):
        word = m.group().lower()
        if word in _PREFIXES and position < 2:
            continue
        near = word in wanted or (not strict and word not in _REAL_WORDS
                                  and difflib.SequenceMatcher(None, word, name).ratio() >= 0.85)
        if near:
            return text[m.end():].lstrip(" ,.!?-:;")
        return None
    return None


def whisper_transcriber(model_name: str) -> Callable[[bytes], str]:
    def run(pcm: bytes) -> str:
        from minty_core.audio.stt import _load_whisper

        model = _load_whisper(model_name)
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = model.transcribe(
            audio, language="en", beam_size=1, initial_prompt="Minty. Hey Minty, what is the time?"
        )
        return " ".join(s.text.strip() for s in segments).strip()

    return run


class TranscriptWakeDetector:
    """Drop-in for OpenWakeWordDetector. `remainder` holds the words spoken after the wake word."""

    def __init__(self, transcribe: Callable[[bytes], str], get_cfg: Callable[[], AudioConfig],
                 on_partial: Callable[[str], None] | None = None,
                 music: Callable[[], bool] | None = None,
                 speech_check: Callable[[bytes], int | None] | None = None, vad=None):
        self._transcribe = transcribe
        self._speech_check = speech_check  # ms of real speech; noise never reaches Whisper (and costs no CPU)
        self._get_cfg = get_cfg
        self._vad = vad  # FrameVAD or None: quiet "Minty" still starts an utterance
        self._floor: float | None = None  # the room's level, learned between utterances
        self._on_partial = on_partial
        self._music = music
        self._last_check = 0.0
        self.remainder = ""
        self.reset()

    def reset(self) -> None:
        self._pre: deque = deque(maxlen=_PRE_ROLL)
        self._buf: list[bytes] = []
        self._speech = 0
        self._silent = 0
        self._skip = getattr(self, "_skip", False)

    def detect(self, frame: bytes) -> bool:
        cfg = self._get_cfg()
        level = rms(frame)
        loud = is_voice(level, speech_threshold(cfg, self._floor), self._floor, frame, self._vad if cfg.vad_assist else None)
        if not loud and not self._buf:
            self._floor = track_floor(self._floor, level)
        if self._skip:  # after an endless stretch, wait for a real pause before listening again
            self._silent = 0 if loud else self._silent + 1
            if self._silent >= _SILENCE_FRAMES:
                self._skip = False
                self._silent = 0
            return False
        if not self._buf:
            if not loud:
                self._pre.append(frame)
                return False
            self._buf = list(self._pre)
            self._pre.clear()
        self._buf.append(frame)
        if loud:
            self._speech += 1
            self._silent = 0
        else:
            self._silent += 1
        if self._silent >= _SILENCE_FRAMES or len(self._buf) >= _MAX_FRAMES:
            return self._evaluate(cfg)
        if (self._on_partial is not None and cfg.live_captions and loud and self._speech >= _MIN_SPEECH_FRAMES
                and self._speech % _CAPTION_EVERY == 0 and len(self._buf) <= _CAPTION_MAX_FRAMES
                and not (self._music and cfg.music_aware and self._music())):  # no captions of lyrics
            self._caption()
        return False

    def _caption(self) -> None:
        try:
            text = self._transcribe(b"".join(self._buf))
            if text:
                self._on_partial(text)
        except Exception as e:  # captions never affect wake detection
            log.debug("live caption failed: %s", e)

    def _evaluate(self, cfg: AudioConfig) -> bool:
        pcm, speech, endless = b"".join(self._buf), self._speech, len(self._buf) >= _MAX_FRAMES
        self.reset()
        self.remainder = ""
        if endless:  # 10 s without a pause is TV or a conversation, not someone calling us
            self._skip = True
            return False
        if speech < _MIN_SPEECH_FRAMES:  # a click or cough
            return False
        music = bool(self._music and cfg.music_aware and self._music())
        if music:  # songs are long and continuous; a person calling us is short. Also don't transcribe non-stop.
            now = time.monotonic()
            if len(pcm) // 2 > _MUSIC_MAX_FRAMES * 1280 or now - self._last_check < _MUSIC_MIN_GAP_S:
                return False
            self._last_check = now
        if self._speech_check is not None:
            voiced = self._speech_check(pcm)
            if voiced is not None and voiced < cfg.min_voiced_ms:
                return False
        try:
            text = self._transcribe(pcm)
        except Exception as e:
            log.warning("wake word check failed: %s", e)
            return False
        rest = match_wake(text, cfg.wake_phrases, strict=music)
        if rest is None:
            return False
        self.remainder = rest
        return True
