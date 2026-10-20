"""Data-policy truth (W0-22): who may train on prompts, who may see a private turn, and where a request went."""
import pytest

from tests.test_router import Clock
from minty_core.commands import CommandRouter
from minty_core.config import ConfigStore, ProviderConfig, default_providers
from minty_core.keystore import SecretsStore
from minty_core.llm.router import ProviderRouter
from minty_core.llm.types import Message, TextDelta
from minty_core.tracereport import where_it_went


def test_defaults_tell_the_truth():
    p = {c.name: c for c in default_providers()}
    for name in ("gemini", "mistral", "openrouter"):
        assert p[name].trains_on_data and not p[name].allow_sensitive
    for name in ("nvidia", "nvidia-deep", "nvidia-gemma", "cerebras"):
        assert not p[name].allow_sensitive and p[name].policy_verified is False
    assert p["groq"].allow_sensitive and not p["groq"].trains_on_data


def test_an_older_config_is_corrected_for_known_hosts():
    old_nim = ProviderConfig(name="nvidia", base_url="https://integrate.api.nvidia.com/v1", model="x",
                             allow_sensitive=True)
    assert old_nim.effective_policy() == {"trains_on_data": False, "allow_sensitive": False, "verified": False}
    old_free = ProviderConfig(name="or", base_url="https://openrouter.ai/api/v1", model="y:free", trains_on_data=False)
    assert old_free.effective_policy()["trains_on_data"] is True
    checked = ProviderConfig(name="nvidia", base_url="https://integrate.api.nvidia.com/v1", model="x",
                             policy_verified=True)
    assert checked.effective_policy()["allow_sensitive"] is True  # the user checked the terms themselves
    unknown = ProviderConfig(name="local", base_url="http://localhost:8080/v1", model="z")
    assert unknown.effective_policy() == {"trains_on_data": False, "allow_sensitive": True, "verified": None}


class Recorder:
    def __init__(self, name, log):
        self.name, self.log = name, log

    async def stream(self, messages, tools):
        self.log.append(self.name)
        yield TextDelta("ok")


def router_for(tmp_path, providers):
    store = ConfigStore(tmp_path)
    data = store.load().model_dump(mode="json")
    data["providers"] = [p.model_dump(mode="json") for p in providers]
    store.update(data)
    secrets = SecretsStore(tmp_path)
    for p in providers:
        secrets.set(p.key_name or p.name, "key-" + p.name)
    log = []
    return ProviderRouter(store, secrets, lambda cfg, key: Recorder(cfg.name, log), Clock()), log


ALL = default_providers() + [
    ProviderConfig(name="old-nim", base_url="https://integrate.api.nvidia.com/v1", model="x", allow_sensitive=True),
    ProviderConfig(name="old-free", base_url="https://openrouter.ai/api/v1", model="y:free", allow_sensitive=True,
                   trains_on_data=False),
]


@pytest.mark.parametrize("provider", ALL, ids=[p.name for p in ALL])
async def test_a_private_turn_never_reaches_a_provider_that_may_not_see_it(tmp_path, provider):
    provider = provider.model_copy(update={"enabled": True, "tier": "fast"})
    router, log = router_for(tmp_path, [provider])
    try:
        [_ async for _ in router.stream([Message("user", "my diary entry")], [], sensitive=True)]
    except Exception:
        pass
    allowed = provider.effective_policy()["allow_sensitive"] and not provider.is_paid()  # paid: W0-24 guard
    assert (log == [provider.name]) == allowed


async def test_status_carries_the_privacy_label(tmp_path):
    router, _ = router_for(tmp_path, default_providers())
    rows = {r["name"]: r for r in router.status()}
    assert rows["gemini"]["privacy"] == "may train on prompts; not for private turns"
    assert rows["nvidia"]["privacy"] == "data use not verified; not for private turns"
    assert rows["groq"]["privacy"] == "does not train on prompts; private turns allowed"


EVENTS = [
    {"kind": "llm_call", "turn_id": "t1", "provider": "openrouter", "outcome": "ok", "ts": 1.0},
    {"kind": "llm_call", "turn_id": "t2", "provider": "mistral", "outcome": "error", "error_kind": "rate_limit",
     "ts": 2.0},
    {"kind": "llm_call", "turn_id": "t2", "provider": "groq", "outcome": "ok", "ts": 3.0},
    {"kind": "turn", "turn_id": "t3", "outcome": "command", "ts": 4.0},
]


def test_where_did_that_go_reads_the_last_turn_with_model_calls():
    policy = {"groq": "does not train on prompts", "mistral": "may train on prompts"}
    reply = where_it_went(EVENTS, policy)
    assert "groq" in reply and "does not train on prompts" in reply and "mistral" in reply
    assert "refused" in reply and "openrouter" not in reply
    assert "nothing" in where_it_went([], policy).lower()


def test_where_did_that_go_is_a_command(tmp_path):
    router, _ = router_for(tmp_path, default_providers())
    commands = CommandRouter(agent=None, where=lambda: "Your last request went to groq.")
    for phrase in ("where did that go", "Minty, where did that request go?", "which model saw that",
                   "who saw that"):
        assert commands.handle(phrase) == "Your last request went to groq."
