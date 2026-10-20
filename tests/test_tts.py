from types import SimpleNamespace

import numpy as np
import pytest

from minty_core.audio.tts import ConfiguredTTS, minty_filter
from minty_core.audio.types import Audio, TTSError
from minty_core.config import ConfigStore


class FakeVoice:
    config = SimpleNamespace(sample_rate=22050)

    def synthesize_stream_raw(self, text):
        yield np.full(1000, 1000, dtype=np.int16).tobytes()
        yield np.full(1000, 1000, dtype=np.int16).tobytes()


def make_store(tmp_path, **tts):
    store = ConfigStore(tmp_path)
    store.load()
    data = store.config.model_dump(mode="json")
    data["tts"].update(tts)
    store.update(data)
    return store


async def test_synthesize_joins_chunks_at_voice_rate(tmp_path):
    loaded = []

    def loader(path):
        loaded.append(path)
        return FakeVoice()

    tts = ConfiguredTTS(make_store(tmp_path, voice_model="/v/ryan.onnx"), loader)
    audio = await tts.synthesize("hello")
    assert audio.rate == 22050 and len(audio.pcm) == 4000
    assert loaded == ["/v/ryan.onnx"]


async def test_no_voice_configured_raises(tmp_path):
    with pytest.raises(TTSError):
        await ConfiguredTTS(make_store(tmp_path), lambda p: FakeVoice()).synthesize("hi")


async def test_loader_failure_is_tts_error(tmp_path):
    def loader(path):
        raise FileNotFoundError(path)

    with pytest.raises(TTSError):
        await ConfiguredTTS(make_store(tmp_path, voice_model="/nope.onnx"), loader).synthesize("hi")


async def test_filter_applied_only_when_enabled(tmp_path):
    plain = await ConfiguredTTS(make_store(tmp_path, voice_model="v"), lambda p: FakeVoice()).synthesize("x")
    store = make_store(tmp_path, voice_model="v", minty_filter=True)
    filtered = await ConfiguredTTS(store, lambda p: FakeVoice()).synthesize("x")
    assert len(filtered.pcm) > len(plain.pcm)


def test_filter_lengthens_and_never_clips():
    # Loud input where pitch+echo sums exceed int16 range without normalisation
    loud = Audio(np.full(2000, 32000, dtype=np.int16).tobytes(), 22050)
    out = minty_filter(loud)
    samples = np.frombuffer(out.pcm, dtype=np.int16)

    # Check sample rate preserved
    assert out.rate == 22050

    # Check output length: int(2000/0.88) + int(22050*0.03)
    expected_len = int(2000 / 0.88) + int(22050 * 0.03)
    assert samples.size == expected_len

    # Check max sample is near full scale (not wrapped), proving normalisation happened
    max_sample = int(np.abs(samples.astype(np.int32)).max())
    assert max_sample >= 32000, f"Max sample {max_sample} should be >= 32000 (near full scale)"
    assert max_sample <= 32767, f"Max sample {max_sample} should not exceed int16 max"

    # Check positive input never wraps to negative
    assert np.all(samples >= 0), "Positive input samples wrapped to negative"


def test_filter_echo_and_pitch():
    # Impulse: 1000 zeros with sample[0]=10000 at 16kHz
    impulse_data = np.zeros(1000, dtype=np.int16)
    impulse_data[0] = 10000
    audio = Audio(impulse_data.tobytes(), 16000)

    out = minty_filter(audio)
    samples = np.frombuffer(out.pcm, dtype=np.int16)

    # Check output length: int(1000/0.88) + int(16000*0.03)
    expected_len = int(1000 / 0.88) + int(16000 * 0.03)
    assert samples.size == expected_len, f"Length {samples.size} != expected {expected_len}"

    # Check pitch shift: first sample should be ~10000 (within 1)
    assert abs(samples[0] - 10000) <= 1, f"samples[0] = {samples[0]}, expected ~10000"

    # Check echo: at delay index, should be ~3500 (0.35 * 10000, within 2)
    delay_idx = int(16000 * 0.03)
    echo_val = samples[delay_idx]
    assert abs(echo_val - 3500) <= 2, f"samples[{delay_idx}] = {echo_val}, expected ~3500"


def test_filter_passes_empty_audio_through():
    empty = Audio(b"", 22050)
    assert minty_filter(empty) == empty


def test_new_piper_api_chunks_are_joined():
    import asyncio
    from types import SimpleNamespace

    from minty_core.audio.tts import ConfiguredTTS

    chunk = lambda b: SimpleNamespace(audio_int16_bytes=b, sample_rate=22050)  # noqa: E731

    class NewVoice:
        config = SimpleNamespace(sample_rate=16000)

        def synthesize(self, text):
            return iter([chunk(b"\x01\x00"), chunk(b"\x02\x00")])

    tts = ConfiguredTTS(None, voice_loader=lambda path: NewVoice())
    audio = tts._run("x.onnx", "hi")
    assert audio.pcm == b"\x01\x00\x02\x00" and audio.rate == 22050


class FakeKokoro:
    def __init__(self):
        self.calls = []

    def create(self, text, voice, speed, lang):
        self.calls.append((text, voice, speed, lang))
        return np.array([0.5, -0.5], dtype=np.float32), 24000


def kokoro_store(tmp_path, monkeypatch, **tts):
    d = tmp_path / "xdg-data" / "minty" / "voices"
    d.mkdir(parents=True)
    (d / "kokoro-v1.0.onnx").write_bytes(b"x")
    (d / "voices-v1.0.bin").write_bytes(b"x")
    return make_store(tmp_path, **tts)


async def test_kokoro_is_used_when_installed(tmp_path, monkeypatch):
    fake = FakeKokoro()
    tts = ConfiguredTTS(kokoro_store(tmp_path, monkeypatch, kokoro_voice="bm_george", speed=1.1),
                        kokoro_loader=lambda m, v: fake)
    audio = await tts.synthesize("Hello there.")
    assert audio.rate == 24000 and len(audio.pcm) == 4
    assert fake.calls == [("Hello there.", "bm_george", 1.1, "en-gb")]


async def test_kokoro_failure_falls_back_to_piper(tmp_path, monkeypatch):
    def broken(m, v):
        raise RuntimeError("bad model")

    store = kokoro_store(tmp_path, monkeypatch, voice_model="v")
    audio = await ConfiguredTTS(store, lambda p: FakeVoice(), kokoro_loader=broken).synthesize("x")
    assert audio.pcm


async def test_missing_kokoro_files_use_piper(tmp_path):
    audio = await ConfiguredTTS(make_store(tmp_path, voice_model="v"), lambda p: FakeVoice(),
                                kokoro_loader=lambda m, v: pytest.fail("must not load")).synthesize("x")
    assert audio.pcm
