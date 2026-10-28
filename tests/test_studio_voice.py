import wave
from pathlib import Path

import numpy as np
import pytest

from minty_core.audio.tts import voices_dir
from minty_core.studio import voice
from minty_core.studio.media import MediaError


class FakeKokoro:
    def __init__(self):
        self.calls = []

    def get_voices(self):
        return ["am_michael", "af_heart", "bf_emma"]

    def create(self, text, voice, speed, lang):
        self.calls.append((text, voice, speed, lang))
        t = np.linspace(0, len(text) * 0.05, int(24000 * len(text) * 0.05), endpoint=False)
        return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), 24000


async def test_a_script_becomes_a_wav_file_and_its_length_is_returned(tmp_path):
    engine = FakeKokoro()
    out = tmp_path / "vo.wav"
    seconds = await voice.voiceover("Did you know a creeper cannot climb ladders?", out, voice="bf_emma", speed=1.1, engine=engine)
    with wave.open(str(out)) as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == 24000
        assert abs(w.getnframes() / w.getframerate() - seconds) < 0.01
    assert engine.calls == [("Did you know a creeper cannot climb ladders?", "bf_emma", 1.1, "en-gb")]


async def test_bad_scripts_and_voices_are_refused_before_anything_is_made(tmp_path):
    engine = FakeKokoro()
    for text in ("", "   ", "hi", "x" * 5000):
        with pytest.raises(MediaError):
            await voice.voiceover(text, tmp_path / "a.wav", engine=engine)
    with pytest.raises(MediaError, match="voice"):
        await voice.voiceover("A perfectly fine script here.", tmp_path / "a.wav", voice="nobody", engine=engine)
    assert engine.calls == [] and not (tmp_path / "a.wav").exists()


async def test_without_a_voice_installed_it_says_what_to_install(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "voices_dir", lambda: tmp_path / "none")
    with pytest.raises(MediaError, match="install_voice"):
        await voice.voiceover("A perfectly fine script here.", tmp_path / "a.wav")


REAL_VOICES = voices_dir()  # taken at import: the test fixtures move XDG_DATA_HOME to a temporary folder


@pytest.mark.skipif(not (REAL_VOICES / "kokoro-v1.0.onnx").is_file(), reason="no Kokoro voice installed")
async def test_the_real_voice_makes_real_speech(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "voices_dir", lambda: REAL_VOICES)
    from minty_core.studio.media import mean_volume, probe
    out = tmp_path / "real.wav"
    seconds = await voice.voiceover("Minecraft parkour is harder than it looks.", out)
    info = await probe(out)
    assert 1.5 < seconds < 6 and abs(info.duration - seconds) < 0.2 and (await mean_volume(out)) > -40
