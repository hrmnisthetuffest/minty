import asyncio
import logging

import httpx
import numpy as np

from minty_core.audio.types import SAMPLE_RATE, STT, STTError, Transcription
from minty_core.audio.wav import pcm_to_wav
from minty_core.config import ConfigStore, SttConfig
from minty_core.httpclient import get_client
from minty_core.keystore import SecretsStore

_hardware = None  # probed once per run, only when stt.auto_model is on


def effective_local_model(cfg: SttConfig, hw=None) -> str:
    """The local recogniser to load: the one you set, or with `stt.auto_model` the size that suits this computer."""
    if not cfg.auto_model:
        return cfg.local_model
    global _hardware
    from minty_core import hwprobe

    if hw is None:
        if _hardware is None:
            _hardware = hwprobe.probe()
        hw = _hardware
    return hwprobe.pick_stt_model(hw) or cfg.local_model

log = logging.getLogger(__name__)

# verbose_json reports a language name ("English"); the config speaks ISO codes
_LANG_NAMES = {
    "english": "en", "hindi": "hi", "punjabi": "pa", "urdu": "ur", "bengali": "bn", "tamil": "ta", "telugu": "te",
    "marathi": "mr", "gujarati": "gu", "spanish": "es", "portuguese": "pt", "french": "fr", "german": "de",
    "italian": "it", "dutch": "nl", "russian": "ru", "arabic": "ar", "chinese": "zh", "japanese": "ja", "korean": "ko",
    "turkish": "tr", "polish": "pl", "indonesian": "id", "malay": "ms", "nepali": "ne", "sinhala": "si",
}


def _lang_code(name: str) -> str:
    name = (name or "").strip().lower()
    return _LANG_NAMES.get(name, name)


class GroqSTT:
    URL = "https://api.groq.com/openai/v1/audio/transcriptions"

    def __init__(self, api_key: str, model: str, client: httpx.AsyncClient | None = None, cfg: SttConfig | None = None):
        self._key = api_key
        self._model = model
        self._client = client or get_client()
        self._cfg = cfg or SttConfig()

    async def transcribe(self, pcm: bytes, rate: int = SAMPLE_RATE) -> str:
        return (await self.transcribe_detailed(pcm, rate)).text

    async def transcribe_detailed(self, pcm: bytes, rate: int = SAMPLE_RATE) -> Transcription:
        cfg = self._cfg
        out = await self._request(pcm, rate, cfg.language)
        allowed = cfg.allowed_languages
        if allowed and out.language and out.language not in allowed:
            preferred = cfg.language or allowed[0]
            log.info("STT heard %r, outside %s; asking again in %r", out.language, allowed, preferred)
            out = await self._request(pcm, rate, preferred)
        return out

    async def _request(self, pcm: bytes, rate: int, language: str) -> Transcription:
        data = {"model": self._model, "response_format": "verbose_json", "temperature": "0"}
        if self._cfg.hotwords.strip():
            data["prompt"] = self._cfg.hotwords.strip()
        if language:
            data["language"] = language
        try:
            resp = await self._client.post(
                self.URL,
                headers={"Authorization": f"Bearer {self._key}"},
                files={"file": ("audio.wav", pcm_to_wav(pcm, rate), "audio/wav")},
                data=data,
            )
        except httpx.HTTPError as e:
            raise STTError(f"groq: {e}") from e
        if resp.status_code != 200:
            raise STTError(f"groq: HTTP {resp.status_code} {resp.text[:200]}")
        try:
            body = resp.json()
            segments = body.get("segments") or []
            return Transcription(
                text=body.get("text", "").strip(),
                no_speech_prob=max((float(s.get("no_speech_prob", 0.0)) for s in segments), default=0.0),
                avg_logprob=(sum(float(s.get("avg_logprob", 0.0)) for s in segments) / len(segments)) if segments else 0.0,
                language=_lang_code(body.get("language") or ""),
            )
        except (ValueError, AttributeError, TypeError) as e:
            raise STTError("groq: malformed response") from e


_MODELS: dict = {}


def _load_whisper(name: str):
    if name not in _MODELS:
        from faster_whisper import WhisperModel

        options = dict(device="cpu", compute_type="int8", cpu_threads=4)
        try:  # a cached model loads without asking Hugging Face for its latest revision (0.41 s -> 0.16 s here)
            _MODELS[name] = WhisperModel(name, local_files_only=True, **options)
        except Exception:  # not downloaded yet: the normal way, once
            _MODELS[name] = WhisperModel(name, local_files_only=False, **options)
    return _MODELS[name]


class LocalSTT:
    def __init__(self, model_name: str, loader=None, cfg: SttConfig | None = None):
        self._name = model_name
        self._loader = loader or _load_whisper
        self._cfg = cfg or SttConfig()

    async def transcribe(self, pcm: bytes, rate: int = SAMPLE_RATE) -> str:
        return (await self.transcribe_detailed(pcm, rate)).text

    async def transcribe_detailed(self, pcm: bytes, rate: int = SAMPLE_RATE) -> Transcription:
        return await asyncio.to_thread(self._run, pcm)

    def _run(self, pcm: bytes) -> Transcription:
        cfg = self._cfg
        try:
            model = self._loader(self._name)
            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            language = "en" if self._name.endswith(".en") else (cfg.language or None)
            segments, info = model.transcribe(
                audio, language=language, beam_size=1, vad_filter=cfg.vad_filter,
                no_speech_threshold=cfg.no_speech_threshold, log_prob_threshold=cfg.log_prob_threshold,
                compression_ratio_threshold=cfg.compression_ratio_threshold, condition_on_previous_text=False,
                hotwords=cfg.hotwords.strip() or None)
            segments = list(segments)
            return Transcription(
                text=" ".join(s.text.strip() for s in segments).strip(),
                no_speech_prob=max((float(getattr(s, "no_speech_prob", 0.0)) for s in segments), default=1.0),
                avg_logprob=(sum(float(getattr(s, "avg_logprob", 0.0)) for s in segments) / len(segments))
                if segments else 0.0,
                language=_lang_code(getattr(info, "language", "") or ""),
            )
        except Exception as e:
            raise STTError(f"local stt: {e}") from e


class FallbackSTT:
    def __init__(self, chain: list[STT]):
        self._chain = chain

    async def transcribe(self, pcm: bytes, rate: int = SAMPLE_RATE) -> str:
        return (await self.transcribe_detailed(pcm, rate)).text

    async def transcribe_detailed(self, pcm: bytes, rate: int = SAMPLE_RATE) -> Transcription:
        last: STTError | None = None
        for stt in self._chain:
            try:
                detailed = getattr(stt, "transcribe_detailed", None)
                if detailed is not None:
                    return await detailed(pcm, rate)
                return Transcription(await stt.transcribe(pcm, rate))
            except STTError as e:
                log.warning("STT %s failed: %s", type(stt).__name__, e)
                last = e
        raise last or STTError("no STT available")


def build_stt_chain(cfg: SttConfig, secrets: SecretsStore, client: httpx.AsyncClient | None = None) -> list[STT]:
    local = LocalSTT(effective_local_model(cfg), cfg=cfg)
    if cfg.local_only or cfg.provider == "local":
        return [local]
    key = secrets.get("groq")
    if not key:
        return [local]
    return [GroqSTT(key, cfg.groq_model, client, cfg=cfg), local]


class DynamicSTT:
    def __init__(self, store: ConfigStore, secrets: SecretsStore, client: httpx.AsyncClient | None = None,
                 extra_hints=None):
        self._store = store
        self._secrets = secrets
        self._client = client
        self._extra_hints = extra_hints  # () -> str: words you corrected, passed to the recogniser as hints

    async def transcribe(self, pcm: bytes, rate: int = SAMPLE_RATE) -> str:
        return (await self.transcribe_detailed(pcm, rate)).text

    async def transcribe_detailed(self, pcm: bytes, rate: int = SAMPLE_RATE) -> Transcription:
        cfg = self._store.config.stt
        extra = self._extra_hints() if self._extra_hints is not None else ""
        if extra:
            cfg = cfg.model_copy(update={"hotwords": f"{cfg.hotwords} {extra}".strip()})
        chain = build_stt_chain(cfg, self._secrets, self._client)
        return await FallbackSTT(chain).transcribe_detailed(pcm, rate)
