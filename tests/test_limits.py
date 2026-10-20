"""Provider limits in config and rate-limit headers read from responses (W0-17)."""
import json

import httpx
import pytest
from pydantic import ValidationError

from minty_core.config import ConfigStore, Limits, ProviderConfig
from minty_core.llm.openai_compat import OpenAICompatProvider
from minty_core.llm.ratelimit import parse_duration, parse_rate_headers
from minty_core.llm.types import LLMError, Message


def defaults(tmp_path):
    store = ConfigStore(tmp_path)
    return {p.name: p for p in store.load().providers}


def test_verified_free_tier_limits_are_the_defaults(tmp_path):
    p = defaults(tmp_path)
    assert p["groq"].limits == Limits(rpm=30, tpm=8000, rpd=1000, tpd=200000)
    assert p["openrouter"].limits == Limits(rpm=20, rpd=50)
    assert p["nvidia"].limits.rpm == 40 and p["nvidia-deep"].limits.rpm == 40
    assert p["mistral"].limits == Limits() and p["gemini"].limits == Limits()  # unknown: nothing assumed


def test_limits_survive_a_save_and_unknown_ones_stay_unset(tmp_path):
    store = ConfigStore(tmp_path)
    store.load()
    store.update(store.config.model_dump(mode="json"))
    again = {p.name: p for p in ConfigStore(tmp_path).load().providers}
    assert again["groq"].limits.tpd == 200000 and again["mistral"].limits.rpm is None


def test_limits_must_be_positive():
    with pytest.raises(ValidationError):
        Limits(rpm=0)
    with pytest.raises(ValidationError):
        Limits(tpd=-5)


def test_an_older_config_without_limits_still_gets_the_known_ones():
    cfg = ProviderConfig(name="groq", base_url="https://api.groq.com/openai/v1", model="openai/gpt-oss-20b")
    assert cfg.effective_limits() == Limits(rpm=30, tpm=8000, rpd=1000, tpd=200000)
    free = ProviderConfig(name="or", base_url="https://openrouter.ai/api/v1", model="some/model:free")
    assert free.effective_limits() == Limits(rpm=20, rpd=50)
    paid = ProviderConfig(name="or2", base_url="https://openrouter.ai/api/v1", model="some/model")
    assert paid.effective_limits() == Limits()


def test_configured_limits_win_over_known_ones():
    cfg = ProviderConfig(name="groq", base_url="https://api.groq.com/openai/v1", model="openai/gpt-oss-120b",
                         limits=Limits(tpd=500000))
    assert cfg.effective_limits() == Limits(tpd=500000)


@pytest.mark.parametrize("text, seconds", [
    ("7.66s", 7.66), ("2m59.56s", 179.56), ("1h2m3s", 3723.0), ("120ms", 0.12), ("30", 30.0), ("", None),
    ("soon", None),
])
def test_durations_in_rate_limit_headers(text, seconds):
    got = parse_duration(text)
    assert got == pytest.approx(seconds) if seconds is not None else got is None


def test_groq_style_headers():
    info = parse_rate_headers({
        "x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-requests": "987",
        "x-ratelimit-reset-requests": "18m43.2s", "x-ratelimit-limit-tokens": "8000",
        "x-ratelimit-remaining-tokens": "7412", "x-ratelimit-reset-tokens": "4.41s"}, now=1000.0)
    assert (info.limit_requests, info.remaining_requests, info.limit_tokens, info.remaining_tokens) == \
        (1000, 987, 8000, 7412)
    assert info.reset_requests_s == pytest.approx(1123.2) and info.reset_tokens_s == pytest.approx(4.41)


def test_openrouter_style_headers_reset_in_epoch_milliseconds():
    info = parse_rate_headers({"X-RateLimit-Limit": "20", "X-RateLimit-Remaining": "0",
                               "X-RateLimit-Reset": "1000030000"}, now=1_000_000.0)
    assert info.limit_requests == 20 and info.remaining_requests == 0
    assert info.reset_requests_s == pytest.approx(30.0)


def test_no_rate_headers_means_no_info():
    assert parse_rate_headers({"content-type": "text/event-stream"}, now=0.0) is None


def provider(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatProvider(ProviderConfig(name="groq", base_url="https://api.test/v1", model="m"), "K", client)


async def test_successful_stream_records_what_the_headers_said():
    body = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'
    p = provider(lambda req: httpx.Response(200, content=body, headers={
        "x-ratelimit-remaining-tokens": "5000", "x-ratelimit-limit-tokens": "8000"}))
    [_ async for _ in p.stream([Message("user", "x")], [])]
    assert p.last_rate.remaining_tokens == 5000 and p.last_rate.limit_tokens == 8000


async def test_a_429_carries_its_rate_info_and_retry_after():
    p = provider(lambda req: httpx.Response(429, text=json.dumps({"error": {"message": "slow"}}), headers={
        "retry-after": "12", "x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "12s"}))
    with pytest.raises(LLMError) as e:
        [_ async for _ in p.stream([Message("user", "x")], [])]
    assert e.value.retry_after == 12.0 and e.value.rate.remaining_requests == 0
    assert p.last_rate is e.value.rate
