"""Per-turn token receipts and the prompt-cache hit tracker (W0-30)."""
import json

import httpx
import pytest

from tests.test_ledger import Wall
from tests.test_router import Clock
from minty_core.commands import CommandRouter
from minty_core.config import ConfigStore, ProviderConfig
from minty_core.keystore import SecretsStore
from minty_core.llm.ledger import CapacityLedger
from minty_core.llm.openai_compat import OpenAICompatProvider
from minty_core.llm.router import ProviderRouter
from minty_core.llm.types import Message, TextDelta, Usage
from minty_core.trace import Tracer, read_events, turn_scope
from minty_core.tracereport import format_report, receipt, summarise

GROQ = ProviderConfig(name="groq", base_url="https://api.groq.com/openai/v1", model="openai/gpt-oss-120b")
MISTRAL = ProviderConfig(name="mistral", base_url="https://api.mistral.ai/v1", model="m")


def sse(*chunks):
    return ("".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n").encode()


def provider(cfg, body, seen):
    def handler(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, content=body)
    return OpenAICompatProvider(cfg, "K", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_usage_is_asked_for_where_known_to_work_and_read_back():
    seen = {}
    body = sse({"choices": [{"delta": {"content": "hi"}}]},
               {"choices": [], "usage": {"prompt_tokens": 1200, "completion_tokens": 80, "total_tokens": 1280,
                                         "prompt_tokens_details": {"cached_tokens": 1000},
                                         "completion_tokens_details": {"reasoning_tokens": 30}}})
    p = provider(GROQ, body, seen)
    [_ async for _ in p.stream([Message("user", "x")], [])]
    assert seen["stream_options"] == {"include_usage": True}
    assert p.last_usage == Usage(tokens_in=1200, tokens_out=80, reasoning=30, cached=1000)


async def test_groq_usage_inside_x_groq_is_read_too():
    seen = {}
    body = sse({"choices": [{"delta": {"content": "hi"}}],
                "x_groq": {"usage": {"prompt_tokens": 900, "completion_tokens": 40}}})
    p = provider(GROQ, body, seen)
    [_ async for _ in p.stream([Message("user", "x")], [])]
    assert p.last_usage.tokens_in == 900 and p.last_usage.tokens_out == 40


async def test_unverified_providers_are_not_sent_the_option_but_usage_is_still_read():
    seen = {}
    body = sse({"choices": [{"delta": {"content": "hi"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 2}})
    p = provider(MISTRAL, body, seen)
    [_ async for _ in p.stream([Message("user", "x")], [])]
    assert "stream_options" not in seen and p.last_usage.tokens_in == 10


async def test_no_usage_reported_means_none():
    p = provider(MISTRAL, sse({"choices": [{"delta": {"content": "hi"}}]}), {})
    [_ async for _ in p.stream([Message("user", "x")], [])]
    assert p.last_usage is None


class Reporting:
    def __init__(self, name, usage):
        self.name, self.last_usage = name, None
        self._usage = usage

    async def stream(self, messages, tools):
        yield TextDelta("ok")
        self.last_usage = self._usage


async def test_the_router_records_real_usage_in_the_ledger_and_the_trace(tmp_path):
    store = ConfigStore(tmp_path)
    data = store.load().model_dump(mode="json")
    data["providers"] = [{"name": "groq", "model": "m"}]
    store.update(data)
    secrets = SecretsStore(tmp_path)
    secrets.set("groq", "key-groq")
    ledger = CapacityLedger(tmp_path / "c.sqlite", clock=Wall())
    usage = Usage(tokens_in=3000, tokens_out=150, reasoning=40, cached=2500)
    router = ProviderRouter(store, secrets, lambda cfg, key: Reporting(cfg.name, usage), Clock(), ledger=ledger)
    router.tracer = Tracer(tmp_path / "t")
    with turn_scope():
        [_ async for _ in router.stream([Message("user", "hi")], [])]
    router.tracer.close()
    assert ledger.used("groq", "m")["tokens"] == 3150
    [call] = [e for e in read_events(tmp_path / "t") if e["kind"] == "llm_call"]
    assert (call["tokens_in"], call["tokens_out"], call["reasoning_tokens"], call["cached_tokens"]) == \
        (3000, 150, 40, 2500)
    assert call["usage_source"] == "provider"


EVENTS = [
    {"kind": "llm_call", "turn_id": "t1", "provider": "groq", "outcome": "ok", "ts": 1, "tokens_in": 500,
     "tokens_out": 20, "usage_source": "provider"},
    {"kind": "llm_call", "turn_id": "t2", "provider": "mistral", "outcome": "error", "ts": 2, "error_kind": "rate_limit"},
    {"kind": "llm_call", "turn_id": "t2", "provider": "groq", "outcome": "ok", "ts": 3, "tokens_in": 3000,
     "tokens_out": 120, "reasoning_tokens": 40, "cached_tokens": 2400, "usage_source": "provider"},
    {"kind": "llm_call", "turn_id": "t2", "provider": "groq", "outcome": "ok", "ts": 4, "tokens_in": 3400,
     "tokens_out": 60, "cached_tokens": 3000, "usage_source": "provider"},
    {"kind": "turn", "turn_id": "t3", "outcome": "command", "ts": 5},
]


def test_receipt_for_the_last_turn_with_model_calls():
    text = receipt(EVENTS)
    assert "6,400 tokens in" in text and "180 out" in text and "2 model calls" in text and "40 for reasoning" in text
    assert "84 percent" in text  # 5 400 of 6 400 input tokens came from the provider's prompt cache
    assert "nothing" in receipt([]).lower()


def test_receipt_says_when_numbers_are_estimates():
    est = [{"kind": "llm_call", "turn_id": "x", "provider": "nvidia", "outcome": "ok", "ts": 1, "tokens_in": 800,
            "tokens_out": 30, "usage_source": "estimate"}]
    assert "about" in receipt(est)


def test_report_totals_tokens_and_the_cache_hit_share():
    s = summarise(EVENTS)
    assert s["tokens_in"] == 6900 and s["tokens_out"] == 200 and s["reasoning_tokens"] == 40
    assert s["cached_share"] == round(5400 / 6900, 3)
    assert "cache" in format_report(s)


@pytest.mark.parametrize("phrase", ["how many tokens was that", "Minty, how many tokens did that use?",
                                    "token receipt"])
def test_voice_question(phrase):
    assert CommandRouter(agent=None, receipt=lambda: "That took 1 token.").handle(phrase) == "That took 1 token."
