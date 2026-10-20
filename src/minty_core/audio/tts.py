import asyncio
import logging
import os
import struct
import time
from pathlib import Path

import httpx
import numpy as np

from minty_core.audio.types import Audio, TTSError
from minty_core.config import ConfigStore

log = logging.getLogger(__name__)


class _NoWordCountNoise(logging.Filter):
    """Kokoro's phonemizer warns 'words count mismatch' on ordinary punctuation; it says nothing useful and fills
    the log. A filter (not a level) because phonemizer resets its logger's level every time it runs."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "words count mismatch" not in record.getMessage()


logging.getLogger("phonemizer").addFilter(_NoWordCountNoise())
_VOICES: dict = {}
_KOKORO: dict = {}
FISH_URL = "https://api.fish.audio/v1/tts"
FISH_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
FISH_REST_S = 600.0  # after a key or credit refusal, use the local voice this long before trying Fish again


def parse_wav(data: bytes) -> Audio:
    """16-bit mono from a WAV file. Streamed WAVs may carry placeholder sizes, so the data chunk runs to the end."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a WAV file")
    pos, fmt = 12, None
    while pos + 8 <= len(data):
        chunk, size = data[pos:pos + 4], int.from_bytes(data[pos + 4:pos + 8], "little")
        start = pos + 8
        if chunk == b"fmt " and start + 16 <= len(data):
            tag, channels, rate = struct.unpack_from("<HHI", data, start)
            fmt = (tag, channels, rate, struct.unpack_from("<H", data, start + 14)[0])
        elif chunk == b"data":
            if fmt is None:
                raise ValueError("WAV audio before its format")
            end = len(data) if size in (0, 0xFFFFFFFF) or start + size > len(data) else start + size
            return _mono16(data[start:end], *fmt)
        pos = start + size + (size & 1)
    raise ValueError("WAV without audio")


def _mono16(raw: bytes, tag: int, channels: int, rate: int, bits: int) -> Audio:
    if tag in (1, 0xFFFE) and bits == 16 and channels == 1:
        return Audio(raw[:len(raw) - len(raw) % 2], int(rate))  # already what the player wants
    if tag in (1, 0xFFFE) and bits == 16:
        x = np.frombuffer(raw[:len(raw) - len(raw) % 2], dtype="<i2").astype(np.float32) / 32768
    elif tag in (3, 0xFFFE) and bits == 32:
        x = np.frombuffer(raw[:len(raw) - len(raw) % 4], dtype="<f4")
    else:
        raise ValueError(f"unsupported WAV sample format ({tag}, {bits} bit)")
    if channels > 1:
        x = x[:x.size - x.size % channels].reshape(-1, channels).mean(axis=1)
    return Audio((np.clip(x, -1.0, 1.0) * 32767).astype(np.int16).tobytes(), int(rate))


class FishTTS:
    """Fish Audio cloud voices. Returns None (use the local voice) instead of raising."""

    def __init__(self, secrets, client: httpx.Client | None = None, clock=time.monotonic):
        self._secrets, self._client, self._clock = secrets, client, clock
        self._rest_until = 0.0

    def synthesize(self, cfg, text: str) -> Audio | None:
        key = self._secrets.get("fish") if self._secrets is not None else None
        if not key or self._clock() < self._rest_until:
            return None
        body = {"text": text, "format": "wav", "latency": cfg.fish_latency, "normalize": True,
                "prosody": {"speed": cfg.speed}}
        if cfg.fish_voice:
            body["reference_id"] = cfg.fish_voice
        if self._client is None:  # no proxy variable may redirect the key; no redirects take it elsewhere
            self._client = httpx.Client(timeout=FISH_TIMEOUT, trust_env=False, follow_redirects=False)
        try:
            resp = self._client.post(FISH_URL, json=body,
                                     headers={"Authorization": f"Bearer {key}", "model": cfg.fish_model})
        except httpx.HTTPError as e:
            log.warning("Fish Audio unreachable (%s); local voice for this sentence", type(e).__name__)
            return None
        if resp.status_code in (401, 402, 403):
            self._rest_until = self._clock() + FISH_REST_S
            why = "out of credit" if resp.status_code == 402 else "key refused"
            log.warning("Fish Audio %s (HTTP %s); local voice for %d minutes", why, resp.status_code, FISH_REST_S // 60)
            return None
        if resp.status_code != 200:
            log.warning("Fish Audio HTTP %s; local voice for this sentence", resp.status_code)
            return None
        try:
            return parse_wav(resp.content)
        except ValueError as e:
            log.warning("Fish Audio sent unreadable audio (%s); local voice for this sentence", e)
            return None


def voices_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "minty" / "voices"


def _load_kokoro(model: str, voices: str):
    key = (model, voices)
    if key not in _KOKORO:
        from kokoro_onnx import Kokoro

        _KOKORO[key] = Kokoro(model, voices)
    return _KOKORO[key]


def _load_piper(path: str):
    if path not in _VOICES:
        from piper import PiperVoice

        _VOICES[path] = PiperVoice.load(path)
    return _VOICES[path]


def minty_filter(audio: Audio) -> Audio:
    x = np.frombuffer(audio.pcm, dtype=np.int16).astype(np.float32)
    if x.size == 0:
        return audio
    n = int(x.size / 0.88)
    deep = np.interp(np.linspace(0, x.size - 1, n), np.arange(x.size), x)
    delay = int(audio.rate * 0.03)
    out = np.zeros(n + delay, dtype=np.float32)
    out[:n] += deep
    out[delay:] += 0.35 * deep
    peak = float(np.abs(out).max())
    if peak > 32767:
        out *= 32767 / peak
    return Audio(out.astype(np.int16).tobytes(), audio.rate)


class ConfiguredTTS:
    def __init__(self, store: ConfigStore, voice_loader=None, kokoro_loader=None, secrets=None, private=None,
                 fish: FishTTS | None = None):
        self._store = store
        self.speed_factor = 1.0  # the voice pipeline's pace and evening profile; 1.0 = the configured speed
        self._loader = voice_loader or _load_piper
        self._kokoro_loader = kokoro_loader or _load_kokoro
        self._private = private or (lambda: False)  # private mode: no text leaves the machine
        self._fish = fish or FishTTS(secrets)

    async def synthesize(self, text: str) -> Audio:
        return await asyncio.to_thread(self.synthesize_sync, text)

    def synthesize_sync(self, text: str) -> Audio:
        cfg = self._store.config.tts
        audio = None
        if cfg.engine == "fish" and not self._private():
            audio = self._fish.synthesize(cfg, text)
        if audio is None and cfg.engine in ("kokoro", "fish"):
            model = cfg.kokoro_model or str(voices_dir() / "kokoro-v1.0.onnx")
            voices = cfg.kokoro_voices or str(voices_dir() / "voices-v1.0.bin")
            if Path(model).is_file() and Path(voices).is_file():
                try:
                    audio = self._kokoro(model, voices, cfg, text)
                except Exception as e:
                    if not cfg.voice_model:
                        raise TTSError(f"tts: {e}") from e
                    log.warning("kokoro failed, falling back to piper: %s", e)
            elif not cfg.voice_model:
                raise TTSError("Kokoro voice files are missing (run scripts/install_voice.sh)")
        if audio is None:
            if not cfg.voice_model:
                raise TTSError("no voice configured (set tts.voice_model to a Piper .onnx file)")
            audio = self._run(cfg.voice_model, text)
        return minty_filter(audio) if cfg.minty_filter else audio

    def _kokoro(self, model: str, voices: str, cfg, text: str) -> Audio:
        engine = self._kokoro_loader(model, voices)
        lang = "en-gb" if cfg.kokoro_voice.startswith("b") else "en-us"
        samples, rate = engine.create(text, voice=cfg.kokoro_voice, speed=max(0.5, min(2.0, cfg.speed * self.speed_factor)),
                                       lang=lang)
        pcm = (np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0) * 32767).astype(np.int16)
        return Audio(pcm.tobytes(), int(rate))

    def _run(self, path: str, text: str) -> Audio:
        try:
            voice = self._loader(path)
            if hasattr(voice, "synthesize_stream_raw"):  # piper-tts 1.2
                pcm = b"".join(voice.synthesize_stream_raw(text))
                return Audio(pcm, voice.config.sample_rate)
            chunks = list(voice.synthesize(text))  # piper-tts >= 1.3
            pcm = b"".join(c.audio_int16_bytes for c in chunks)
            return Audio(pcm, chunks[0].sample_rate if chunks else voice.config.sample_rate)
        except Exception as e:
            raise TTSError(f"tts: {e}") from e
