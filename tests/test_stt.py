from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from minty_core.audio.stt import DynamicSTT, FallbackSTT, GroqSTT, LocalSTT, build_stt_chain
from minty_core.audio.types import STTError
from minty_core.config import ConfigStore, SttConfig
from minty_core.keystore import SecretsStore

PCM = np.full(1600, 500, dtype=np.int16).tobytes()


def groq(handler):
    return GroqSTT("KEY", "whisper-large-v3-turbo", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_groq_posts_wav_and_returns_text():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["authorization"]
        seen["body"] = req.content
        return httpx.Response(200, json={"text": " hello there "})

    assert await groq(handler).transcribe(PCM) == "hello there"
    assert seen["url"] == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert seen["auth"] == "Bearer KEY"
    assert b"RIFF" in seen["body"] and b"whisper-large-v3-turbo" in seen["body"]


async def test_groq_http_error_is_stt_error():
    with pytest.raises(STTError):
        await groq(lambda req: httpx.Response(500, text="bad")).transcribe(PCM)


async def test_groq_network_error_is_stt_error():
    def handler(req):
        raise httpx.ConnectError("down")

    with pytest.raises(STTError):
        await groq(handler).transcribe(PCM)


async def test_groq_malformed_response_is_stt_error():
    with pytest.raises(STTError):
        await groq(lambda req: httpx.Response(200, text="<html>")).transcribe(PCM)


async def test_groq_malformed_response_triggers_fallback():
    groq_inst = groq(lambda req: httpx.Response(200, text="<html>"))
    local = Stub("from local")
    result = await FallbackSTT([groq_inst, local]).transcribe(PCM)
    assert result == "from local"


async def test_local_stt_uses_loader_and_english_flag():
    seen = {}

    class FakeModel:
        def transcribe(self, audio, language=None, beam_size=1, **kwargs):
            seen["language"], seen["dtype"], seen["max"] = language, audio.dtype, float(abs(audio).max())
            return [SimpleNamespace(text=" hi "), SimpleNamespace(text="there")], None

    stt = LocalSTT("base.en", loader=lambda name: FakeModel())
    assert await stt.transcribe(PCM) == "hi there"
    assert seen["language"] == "en" and seen["dtype"] == np.float32 and seen["max"] < 1.0


async def test_local_stt_wraps_failures():
    def loader(name):
        raise ImportError("no faster_whisper")

    with pytest.raises(STTError):
        await LocalSTT("base.en", loader=loader).transcribe(PCM)


class Stub:
    def __init__(self, result):
        self.result, self.calls = result, 0

    async def transcribe(self, pcm, rate=16000):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


async def test_fallback_chain():
    a, b = Stub(STTError("rate limited")), Stub("from local")
    assert await FallbackSTT([a, b]).transcribe(PCM) == "from local"
    assert (a.calls, b.calls) == (1, 1)
    with pytest.raises(STTError):
        await FallbackSTT([Stub(STTError("x")), Stub(STTError("y"))]).transcribe(PCM)


def test_build_chain_rules(tmp_path):
    secrets = SecretsStore(tmp_path)
    kinds = lambda cfg: [type(s).__name__ for s in build_stt_chain(cfg, secrets)]
    assert kinds(SttConfig()) == ["LocalSTT"]  # no groq key
    secrets.set("groq", "gsk_abcdefghijklmnop")
    assert kinds(SttConfig()) == ["GroqSTT", "LocalSTT"]
    assert kinds(SttConfig(local_only=True)) == ["LocalSTT"]
    assert kinds(SttConfig(provider="local")) == ["LocalSTT"]


async def test_dynamic_stt_follows_config(tmp_path, monkeypatch):
    store = ConfigStore(tmp_path)
    store.load()
    secrets = SecretsStore(tmp_path)
    secrets.set("groq", "gsk_abcdefghijklmnop")
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"text": "cloud"})))
    dyn = DynamicSTT(store, secrets, client)
    assert await dyn.transcribe(PCM) == "cloud"

    class NeverGroq(GroqSTT):
        async def transcribe_detailed(self, pcm, rate=16000):
            raise AssertionError("cloud used despite local_only")

    monkeypatch.setattr("minty_core.audio.stt.GroqSTT", NeverGroq)
    monkeypatch.setattr("minty_core.audio.stt.LocalSTT.transcribe_detailed", lambda self, pcm, rate=16000: _local())
    data = store.config.model_dump(mode="json")
    data["stt"]["local_only"] = True
    store.update(data)
    assert await dyn.transcribe(PCM) == "local text"


async def _local():
    from minty_core.audio.types import Transcription
    return Transcription("local text")
