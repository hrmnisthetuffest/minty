from dataclasses import dataclass
from typing import Protocol

SAMPLE_RATE = 16000
FRAME_SAMPLES = 1280
FRAME_BYTES = FRAME_SAMPLES * 2
FRAME_S = FRAME_SAMPLES / SAMPLE_RATE


@dataclass(frozen=True)
class Audio:
    pcm: bytes
    rate: int


class STTError(Exception):
    pass


class TTSError(Exception):
    pass


class AudioInput(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def read(self) -> bytes: ...


class WakeWord(Protocol):
    def detect(self, frame: bytes) -> bool: ...
    def reset(self) -> None: ...


class Endpointer(Protocol):
    heard_speech: bool

    def reset(self) -> None: ...
    def feed(self, frame: bytes) -> bool: ...


@dataclass
class Transcription:
    """Words plus how sure the recogniser was. no_speech_prob near 1 means it was probably not speech."""
    text: str
    no_speech_prob: float = 0.0
    avg_logprob: float = 0.0
    language: str = ""  # ISO code the recogniser reported, "" if it did not say


class STT(Protocol):
    async def transcribe(self, pcm: bytes, rate: int = SAMPLE_RATE) -> str: ...


class TTS(Protocol):
    async def synthesize(self, text: str) -> Audio: ...


class Player(Protocol):
    async def play(self, audio: Audio) -> None: ...
    def stop(self) -> None: ...
