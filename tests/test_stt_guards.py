"""Speech-to-text reports confidence and uses Whisper's noise guards."""
from types import SimpleNamespace as NS

import httpx

from minty_core.audio.stt import FallbackSTT, GroqSTT, LocalSTT
from minty_core.audio.types import Transcription
from minty_core.config import SttConfig

PCM = b"\x10\x00" * 1600


async def test_local_stt_passes_guards_and_reports_confidence():
    seen = {}

    class Model:
        def transcribe(self, audio, **kwargs):
            seen.update(kwargs)
            return iter([NS(text=" turn ", no_speech_prob=0.1, avg_logprob=-0.2),
                         NS(text="it up", no_speech_prob=0.3, avg_logprob=-0.4)]), None

    cfg = SttConfig(hotwords="Minty, Alex")
    out = await LocalSTT("base.en", loader=lambda n: Model(), cfg=cfg).transcribe_detailed(PCM)
    assert out == Transcription("turn it up", no_speech_prob=0.3, avg_logprob=-0.30000000000000004) or \
        (out.text == "turn it up" and out.no_speech_prob == 0.3 and abs(out.avg_logprob + 0.3) < 1e-9)
    assert seen["vad_filter"] is True and seen["no_speech_threshold"] == 0.6 and seen["log_prob_threshold"] == -1.0
    assert seen["compression_ratio_threshold"] == 2.4 and seen["condition_on_previous_text"] is False
    assert seen["hotwords"] == "Minty, Alex"


async def test_local_stt_with_no_segments_is_no_speech():
    class Model:
        def transcribe(self, audio, **kwargs):
            return iter([]), None

    out = await LocalSTT("base.en", loader=lambda n: Model()).transcribe_detailed(PCM)
    assert out.text == "" and out.no_speech_prob == 1.0


async def test_groq_asks_for_verbose_json_and_reads_confidence():
    seen = {}

    def handler(req):
        seen["body"] = req.content
        return httpx.Response(200, json={"text": " stop ", "segments": [
            {"no_speech_prob": 0.05, "avg_logprob": -0.1}, {"no_speech_prob": 0.7, "avg_logprob": -1.3}]})

    stt = GroqSTT("K", "whisper-large-v3-turbo", httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                  cfg=SttConfig(hotwords="Minty"))
    out = await stt.transcribe_detailed(PCM)
    assert out.text == "stop" and out.no_speech_prob == 0.7 and abs(out.avg_logprob + 0.7) < 1e-9
    assert b"verbose_json" in seen["body"] and b"Minty" in seen["body"]
    assert await stt.transcribe(PCM) == "stop"


async def test_fallback_wraps_plain_stts():
    class Plain:
        async def transcribe(self, pcm, rate=16000):
            return "hello"

    out = await FallbackSTT([Plain()]).transcribe_detailed(PCM)
    assert out == Transcription("hello")
