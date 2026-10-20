"""Frugal mode F1 plumbing (W0-27): fewer reasoning and output tokens on providers that support it."""
import json

import httpx
import pytest

from tests.test_router import Clock
from minty_core.commands import CommandRouter
from minty_core.config import ConfigStore, ProviderConfig
from minty_core.frugal import frugal_params
from minty_core.keystore import SecretsStore
from minty_core.llm.openai_compat import OpenAICompatProvider
from minty_core.llm.router import ProviderRouter
from minty_core.llm.types import Message, TextDelta
from minty_core.trace import Tracer, read_events, turn_scope

GROQ = {"name": "groq", "base_url": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b",
        "small_model": "openai/gpt-oss-20b"}
OTHER = {"name": "nvidia", "base_url": "https://integrate.api.nvidia.com/v1", "model": "nemotron"}


def test_policy_per_turn_type():
    assert frugal_params("F0", "small", "fast") == {}
    small, large = frugal_params("F1", "small", "fast"), frugal_params("F1", "large", "fast")
    assert small == {"reasoning_effort": "low", "max_completion_tokens": 600, "include_reasoning": False}
    assert large["reasoning_effort"] == "medium" and large["max_completion_tokens"] > small["max_completion_tokens"]
    assert frugal_params("F1", "large", "deep") == {}  # thinking mode keeps its full effort


def test_only_known_reasoning_models_take_the_parameters():
    assert ProviderConfig(**GROQ).effective_reasoning_support()
    assert not ProviderConfig(**OTHER).effective_reasoning_support()
    assert ProviderConfig(**OTHER, supports_reasoning_effort=True).effective_reasoning_support()


def test_frugal_defaults_to_off_until_the_evaluation_says_otherwise(tmp_path):
    cfg = ConfigStore(tmp_path).load()
    assert cfg.frugal.level == "F0" and cfg.frugal.auto_in_brownout


class Recorder:
    def __init__(self, cfg, log):
        self.cfg, self.log = cfg, log

    async def stream(self, messages, tools):
        self.log.append((self.cfg.name, self.cfg.model, dict(self.cfg.extra)))
        yield TextDelta("ok")


def make(tmp_path, providers, level="F0", low=lambda: False):
    store = ConfigStore(tmp_path)
    data = store.load().model_dump(mode="json")
    data["providers"] = providers
    data["frugal"]["level"] = level
    store.update(data)
    secrets = SecretsStore(tmp_path)
    for p in providers:
        secrets.set(p["name"], "key-" + p["name"])
    log = []
    router = ProviderRouter(store, secrets, lambda cfg, key: Recorder(cfg, log), Clock())
    router.capacity_low = low
    return router, log


async def ask(router, text, tier="fast", prefer=None):
    return [e async for e in router.stream([Message("user", text)], [], tier=tier, prefer=prefer)]


async def test_f1_sends_the_parameters_to_groq(tmp_path):
    router, log = make(tmp_path, [GROQ], level="F1")
    await ask(router, "what time is it")
    await ask(router, "plan my revision week for the maths exam")
    (_, small_model, small), (_, large_model, large) = log
    assert small_model == "openai/gpt-oss-20b" and small["reasoning_effort"] == "low"
    assert large_model == "openai/gpt-oss-120b" and large["reasoning_effort"] == "medium"
    assert small["include_reasoning"] is False


async def test_providers_without_support_get_nothing_extra(tmp_path):
    router, log = make(tmp_path, [OTHER], level="F1")
    await ask(router, "hi")
    assert log[0][2] == {}


async def test_f0_sends_nothing(tmp_path):
    router, log = make(tmp_path, [GROQ], level="F0")
    await ask(router, "hi")
    assert "reasoning_effort" not in log[0][2]


async def test_brownout_turns_f1_on_by_itself(tmp_path):
    router, log = make(tmp_path, [GROQ], level="F0", low=lambda: True)
    await ask(router, "hi")
    assert log[0][2]["reasoning_effort"] == "low"


async def test_the_level_is_traced(tmp_path):
    router, _ = make(tmp_path, [GROQ], level="F1")
    router.tracer = Tracer(tmp_path / "t")
    with turn_scope():
        await ask(router, "hi")
    router.tracer.close()
    [call] = [e for e in read_events(tmp_path / "t") if e["kind"] == "llm_call"]
    assert call["frugal"] == "F1"


async def test_parameters_reach_the_request_body():
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    cfg = ProviderConfig(**GROQ, extra={"reasoning_effort": "low", "max_completion_tokens": 600,
                                        "include_reasoning": False})
    p = OpenAICompatProvider(cfg, "K", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    [_ async for _ in p.stream([Message("user", "hi")], [])]
    assert seen["reasoning_effort"] == "low" and seen["max_completion_tokens"] == 600
    assert seen["include_reasoning"] is False


@pytest.mark.parametrize("phrase, level", [("frugal mode on", "F1"), ("Minty, frugal mode off", "F0"),
                                           ("turn on frugal mode", "F1"), ("save tokens", "F1")])
def test_voice_switch(tmp_path, phrase, level):
    store = ConfigStore(tmp_path)
    store.load()
    reply = CommandRouter(agent=None, store=store).handle(phrase)
    assert reply and store.config.frugal.level == level
