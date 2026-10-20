"""Usage-based routing and the complexity router (W0-19)."""
import pytest

from tests.test_ledger import Wall
from tests.test_router import Clock
from minty_core.complexity import classify
from minty_core.config import ConfigStore, ProviderConfig
from minty_core.keystore import SecretsStore
from minty_core.llm.ledger import CapacityLedger
from minty_core.llm.router import ProviderRouter
from minty_core.llm.types import Message, TextDelta, ToolCall

GROQ = {"name": "groq", "base_url": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b",
        "small_model": "openai/gpt-oss-20b", "limits": {"rpm": 30, "tpm": 8000, "rpd": 1000, "tpd": 200000}}
NIM = {"name": "nvidia", "base_url": "https://integrate.api.nvidia.com/v1", "model": "nemotron", "limits": {"rpm": 40}}


@pytest.mark.parametrize("text, size", [
    ("hi", "small"), ("thank you", "small"), ("what time is it", "small"), ("what's the weather", "small"),
    ("click store", "small"), ("set a timer for ten minutes", "small"), ("play some jazz", "small"),
    ("plan my week of revision for the maths exam", "large"),
    ("compare these two phones and tell me which is better", "large"),
    ("open amazon and find the cheapest desk lamp", "large"),
    ("write an email to my teacher about the trip", "large"),
    ("open chrome, then search for cats and then click the first result", "large"),
    ("why is the sky blue", "large"),
    (" ".join(["word"] * 30), "large"),
])
def test_classify(text, size):
    assert classify([Message("user", text)]) == size


def test_a_chain_of_tool_calls_is_large():
    msgs = [Message("user", "click store"),
            Message("assistant", "", tool_calls=[ToolCall("1", "browser_click", {})]), Message("tool", "ok", "", None),
            Message("assistant", "", tool_calls=[ToolCall("2", "browser_click", {})]), Message("tool", "ok")]
    assert classify(msgs) == "large"
    assert classify(msgs[:3]) == "small"


def test_an_older_config_gets_the_known_small_model():
    cfg = ProviderConfig(name="groq", base_url="https://api.groq.com/openai/v1", model="openai/gpt-oss-120b")
    assert cfg.effective_small_model() == "openai/gpt-oss-20b"
    assert ProviderConfig(name="x", base_url="https://x/v1", model="m").effective_small_model() == ""


class Recorder:
    def __init__(self, name, model, log):
        self.name, self.model, self.log = name, model, log

    async def stream(self, messages, tools):
        self.log.append((self.name, self.model))
        yield TextDelta("ok")


def make(tmp_path, providers, ledger=None, **router_cfg):
    store = ConfigStore(tmp_path)
    data = store.load().model_dump(mode="json")
    data["providers"] = providers
    data["router"].update(router_cfg)
    store.update(data)
    secrets = SecretsStore(tmp_path)
    for p in providers:
        secrets.set(p["name"], "key-" + p["name"])
    log = []
    router = ProviderRouter(store, secrets, lambda cfg, key: Recorder(cfg.name, cfg.model, log), Clock(),
                            ledger=ledger)
    return router, log


async def ask(router, text):
    return [e async for e in router.stream([Message("user", text)], [])]


async def test_simple_turns_use_the_small_model_and_hard_ones_the_large(tmp_path):
    router, log = make(tmp_path, [GROQ])
    await ask(router, "what time is it")
    await ask(router, "plan my week of revision for the maths exam")
    assert log == [("groq", "openai/gpt-oss-20b"), ("groq", "openai/gpt-oss-120b")]


async def test_complexity_switched_off_always_uses_the_configured_model(tmp_path):
    router, log = make(tmp_path, [GROQ], complexity=False)
    await ask(router, "hi")
    assert log == [("groq", "openai/gpt-oss-120b")]


async def test_thinking_mode_never_downsizes(tmp_path):
    router, log = make(tmp_path, [dict(GROQ, tier="deep")])
    [_ async for _ in router.stream([Message("user", "hi")], [], tier="deep")]
    assert log == [("groq", "openai/gpt-oss-120b")]


async def test_a_provider_near_its_daily_limit_gives_way(tmp_path):
    ledger = CapacityLedger(tmp_path / "c.sqlite", clock=Wall())
    ledger.record("groq", "openai/gpt-oss-120b", tokens_in=181000, tokens_out=0)  # 90 % of 200 000 used
    router, log = make(tmp_path, [GROQ, NIM], ledger)
    await ask(router, "plan my week of revision for the maths exam")
    assert log[0][0] == "nvidia"


async def test_a_provider_that_cannot_afford_the_request_goes_last_even_if_picked(tmp_path):
    ledger = CapacityLedger(tmp_path / "c.sqlite", clock=Wall())
    ledger.record("groq", "openai/gpt-oss-20b", tokens_in=200000, tokens_out=0)
    router, log = make(tmp_path, [GROQ, NIM], ledger)
    data = router._store.config.model_dump(mode="json")
    data["preferred_provider"] = "groq"
    router._store.update(data)
    await ask(router, "what time is it")
    assert log[0][0] == "nvidia"


async def test_headroom_ordering_switched_off(tmp_path):
    ledger = CapacityLedger(tmp_path / "c.sqlite", clock=Wall())
    ledger.record("groq", "openai/gpt-oss-120b", tokens_in=190000, tokens_out=0)
    router, log = make(tmp_path, [GROQ, NIM], ledger, usage_order=False)
    await ask(router, "plan my week of revision for the maths exam")
    assert log[0][0] == "groq"


@pytest.mark.parametrize("near_limit", [True, False])
async def test_private_turns_still_never_reach_a_disallowed_provider(tmp_path, near_limit):
    ledger = CapacityLedger(tmp_path / "c.sqlite", clock=Wall())
    if near_limit:
        ledger.record("groq", "openai/gpt-oss-20b", tokens_in=199000, tokens_out=0)
    leaky = dict(NIM, name="leaky", allow_sensitive=False)
    router, log = make(tmp_path, [leaky, GROQ], ledger)
    [_ async for _ in router.stream([Message("user", "my diary")], [], sensitive=True)]
    assert all(name != "leaky" for name, _m in log)
