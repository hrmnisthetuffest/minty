"""STT language pinning (W0-01): English by default, an allow-list, one retry when the language comes back wrong."""
import re
from types import SimpleNamespace as NS

import httpx
import pytest
from pydantic import ValidationError

from minty_core.audio.stt import GroqSTT, LocalSTT
from minty_core.config import SttConfig

PCM = b"\x10\x00" * 1600


def fields(body: bytes) -> dict[str, str]:
    """The plain form fields of a multipart body (the audio file part is skipped)."""
    out = {}
    for name, value in re.findall(rb'name="([^"]+)"\r\n\r\n(.*?)\r\n--', body, re.S):
        out[name.decode()] = value.decode(errors="replace")
    return out


def groq(responses: list[dict], cfg: SttConfig, calls: list[dict]):
    def handler(req):
        calls.append(fields(req.content))
        return httpx.Response(200, json=responses[min(len(calls), len(responses)) - 1])

    return GroqSTT("K", "whisper-large-v3-turbo", httpx.AsyncClient(transport=httpx.MockTransport(handler)), cfg=cfg)


def test_defaults_pin_english():
    cfg = SttConfig()
    assert cfg.language == "en" and cfg.allowed_languages == ["en"]


def test_language_codes_are_normalised():
    cfg = SttConfig(language="EN", allowed_languages=[" en", "HI"])
    assert cfg.language == "en" and cfg.allowed_languages == ["en", "hi"]
    with pytest.raises(ValidationError):
        SttConfig(language="english please")


async def test_groq_request_carries_language():
    calls: list[dict] = []
    out = await groq([{"text": "hello", "language": "English"}], SttConfig(), calls).transcribe_detailed(PCM)
    assert calls[0]["language"] == "en" and calls[0]["prompt"] == "Minty"
    assert out.text == "hello" and out.language == "en" and len(calls) == 1


async def test_empty_language_means_auto_detect():
    calls: list[dict] = []
    cfg = SttConfig(language="", allowed_languages=[])
    out = await groq([{"text": "olá", "language": "portuguese"}], cfg, calls).transcribe_detailed(PCM)
    assert "language" not in calls[0] and out.text == "olá" and len(calls) == 1


async def test_language_outside_allow_list_retries_once_with_preferred():
    calls: list[dict] = []
    cfg = SttConfig(language="", allowed_languages=["en", "hi"])
    stt = groq([{"text": "abre o chrome", "language": "portuguese"}, {"text": "open Chrome", "language": "english"}],
               cfg, calls)
    out = await stt.transcribe_detailed(PCM)
    assert len(calls) == 2 and "language" not in calls[0] and calls[1]["language"] == "en"
    assert out.text == "open Chrome" and out.language == "en"


async def test_retry_happens_at_most_once():
    calls: list[dict] = []
    cfg = SttConfig(language="", allowed_languages=["en"])
    out = await groq([{"text": "hola", "language": "spanish"}], cfg, calls).transcribe_detailed(PCM)
    assert len(calls) == 2 and out.text == "hola"


async def test_allowed_detected_language_is_kept():
    calls: list[dict] = []
    cfg = SttConfig(language="", allowed_languages=["en", "hi"])
    out = await groq([{"text": "namaste", "language": "Hindi"}], cfg, calls).transcribe_detailed(PCM)
    assert len(calls) == 1 and out.language == "hi"


async def test_missing_language_in_response_never_retries():
    calls: list[dict] = []
    await groq([{"text": "hi"}], SttConfig(language="", allowed_languages=["en"]), calls).transcribe_detailed(PCM)
    assert len(calls) == 1


class Model:
    def __init__(self):
        self.seen = {}

    def transcribe(self, audio, **kwargs):
        self.seen.update(kwargs)
        return iter([NS(text="hi", no_speech_prob=0.1, avg_logprob=-0.1)]), NS(language="en")


async def test_local_multilingual_model_gets_configured_language():
    m = Model()
    out = await LocalSTT("base", loader=lambda n: m, cfg=SttConfig(language="hi", allowed_languages=["hi"])) \
        .transcribe_detailed(PCM)
    assert m.seen["language"] == "hi" and out.language == "en"


async def test_local_english_model_always_english_and_auto_stays_auto():
    m = Model()
    await LocalSTT("base.en", loader=lambda n: m, cfg=SttConfig(language="hi")).transcribe_detailed(PCM)
    assert m.seen["language"] == "en"
    m2 = Model()
    await LocalSTT("base", loader=lambda n: m2, cfg=SttConfig(language="", allowed_languages=[])).transcribe_detailed(PCM)
    assert m2.seen["language"] is None
