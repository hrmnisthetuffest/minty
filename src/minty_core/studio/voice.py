"""Voiceover: a script spoken by the local Kokoro voice into a WAV file. Offline; nothing leaves the computer."""
from __future__ import annotations

import asyncio
import wave
from pathlib import Path

import numpy as np

from minty_core.audio.tts import _load_kokoro, voices_dir
from minty_core.studio.media import MediaError

MIN_CHARS, MAX_CHARS = 8, 4000


def _engine():
    model, voices = voices_dir() / "kokoro-v1.0.onnx", voices_dir() / "voices-v1.0.bin"
    if not (model.is_file() and voices.is_file()):
        raise MediaError("No voice is installed for voiceovers. Run scripts/install_voice.sh to get the Kokoro voice (about 330 MB).")
    return _load_kokoro(str(model), str(voices))


def _make(text: str, out: Path, voice: str, speed: float, engine) -> float:
    engine = engine or _engine()
    known = engine.get_voices() if hasattr(engine, "get_voices") else None
    if known is not None and voice not in known:
        raise MediaError(f"unknown voice {voice!r}; pick one of: {', '.join(sorted(known)[:12])}")
    samples, rate = engine.create(text, voice=voice, speed=max(0.5, min(2.0, float(speed))),
                                  lang="en-gb" if voice.startswith("b") else "en-us")
    pcm = (np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0) * 32767).astype(np.int16)
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.stem + ".part" + out.suffix)
    with wave.open(str(part), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm.tobytes())
    part.replace(out)
    return len(pcm) / float(rate)


async def voiceover(text: str, out: Path, *, voice: str = "am_michael", speed: float = 1.0, engine=None) -> float:
    """Speak the script into `out`; returns its length in seconds."""
    text = " ".join(str(text or "").split())
    if not MIN_CHARS <= len(text) <= MAX_CHARS:
        raise MediaError(f"the script must be {MIN_CHARS}-{MAX_CHARS} characters")
    return await asyncio.to_thread(_make, text, Path(out), voice, speed, engine)
