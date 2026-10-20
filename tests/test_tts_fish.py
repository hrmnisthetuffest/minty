import json
import struct

import httpx
import numpy as np

from minty_core.audio.tts import ConfiguredTTS, FishTTS, parse_wav
from minty_core.audio.wav import pcm_to_wav
from minty_core.config import ConfigStore

PCM = (np.arange(240, dtype=np.int16) * 10).tobytes()


class Secrets:
    def __init__(self, key="fish-test-key"):
        self.key = key

    def get(self, name):
        return self.key if name == "fish" else None


def store(tmp_path, **tts):
    s = ConfigStore(tmp_path)
    data = s.load().model_dump(mode="json")
    data["tts"].update(engine="fish", voice_model="", kokoro_model=str(tmp_path / "k.onnx"),
                       kokoro_voices=str(tmp_path / "v.bin"), **tts)
    (tmp_path / "k.onnx").write_bytes(b"x")
    (tmp_path / "v.bin").write_bytes(b"x")
    s.update(data)
    return s


class Kokoro:
    def create(self, text, voice, speed, lang):
        return np.zeros(10, dtype=np.float32), 24000


def build(tmp_path, handler, private=False, key="fish-test-key", now=lambda: 0.0, **tts):
    seen = []

    def record(request):
        seen.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(record))
    tts_ = ConfiguredTTS(store(tmp_path, **tts), kokoro_loader=lambda m, v: Kokoro(), private=lambda: private,
                         fish=FishTTS(Secrets(key), client=client, clock=now))
    return tts_, seen


def test_fish_request_shape_and_audio(tmp_path):
    tts, seen = build(tmp_path, lambda r: httpx.Response(200, content=pcm_to_wav(PCM, 44100)), fish_voice="abc123")
    audio = tts.synthesize_sync("Hello there.")
    assert audio.pcm == PCM and audio.rate == 44100
    [req] = seen
    body = json.loads(req.content)
    assert req.headers["authorization"] == "Bearer fish-test-key" and req.headers["model"] == "s2.1-pro"
    assert body["text"] == "Hello there." and body["reference_id"] == "abc123" and body["format"] == "wav"


def test_streamed_wav_with_placeholder_sizes_and_stereo_float():
    wav = pcm_to_wav(PCM, 22050)
    streamed = wav[:4] + b"\xff\xff\xff\xff" + wav[8:40] + b"\xff\xff\xff\xff" + wav[44:]
    assert parse_wav(streamed).pcm == PCM
    stereo = np.array([0.5, 0.5, -0.5, -0.5], dtype="<f4").tobytes()
    fmt = struct.pack("<HHIIHH", 3, 2, 48000, 48000 * 8, 8, 32)
    wav32 = b"RIFF" + b"\0\0\0\0WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", 16) + stereo
    out = parse_wav(wav32)
    assert out.rate == 48000 and np.frombuffer(out.pcm, dtype=np.int16).tolist() == [16383, -16383]


def test_out_of_credit_falls_back_and_rests(tmp_path):
    t = [0.0]
    tts, seen = build(tmp_path, lambda r: httpx.Response(402), now=lambda: t[0])
    assert tts.synthesize_sync("one").rate == 24000  # the local voice spoke
    tts.synthesize_sync("two")
    assert len(seen) == 1  # resting: no request per sentence
    t[0] = 601.0
    tts.synthesize_sync("three")
    assert len(seen) == 2


def test_private_mode_and_missing_key_never_call_fish(tmp_path):
    (tmp_path / "a").mkdir()
    tts, seen = build(tmp_path / "a", lambda r: httpx.Response(200), private=True)
    assert tts.synthesize_sync("secret").rate == 24000 and seen == []
    (tmp_path / "b").mkdir()
    tts, seen = build(tmp_path / "b", lambda r: httpx.Response(200), key=None)
    assert tts.synthesize_sync("hi").rate == 24000 and seen == []


def test_network_error_falls_back(tmp_path):
    def boom(request):
        raise httpx.ConnectError("down")

    tts, _ = build(tmp_path, boom)
    assert tts.synthesize_sync("hi").rate == 24000
