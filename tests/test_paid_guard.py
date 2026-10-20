"""Paid-model guard (W0-24): a paid provider is used only when switched on, within its monthly cap, and for a
capability it was opted into. Ordinary turns stay free."""
from tests.test_ledger import Wall
from tests.test_router import Clock
from minty_core.config import ConfigStore, ProviderConfig, default_providers
from minty_core.keystore import SecretsStore
from minty_core.llm.ledger import CapacityLedger
from minty_core.llm.router import ProviderRouter
from minty_core.llm.types import LLMError, Message, TextDelta

FREE = {"name": "groq", "base_url": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b"}
PAID = {"name": "claude", "kind": "anthropic", "model": "claude-sonnet-5-5", "enabled": True, "paid": True,
        "monthly_cap_usd": 5.0, "price_per_mtok_usd": 6.0, "capabilities": ["coding"]}


class Recorder:
    def __init__(self, name, log):
        self.name, self.log = name, log

    async def stream(self, messages, tools):
        self.log.append(self.name)
        yield TextDelta("ok")


def make(tmp_path, providers, ledger=None):
    store = ConfigStore(tmp_path)
    data = store.load().model_dump(mode="json")
    data["providers"] = providers
    store.update(data)
    secrets = SecretsStore(tmp_path)
    for p in providers:
        secrets.set(p["name"], "key-" + p["name"])
    log = []
    return ProviderRouter(store, secrets, lambda cfg, key: Recorder(cfg.name, log), Clock(), ledger=ledger), log


async def ask(router, capability=None):
    try:
        return [e async for e in router.stream([Message("user", "fix this bug")], [], capability=capability)]
    except LLMError:
        return []


def test_the_anthropic_default_is_paid_with_no_budget():
    claude = next(p for p in default_providers() if p.kind == "anthropic")
    assert claude.paid and claude.monthly_cap_usd == 0 and not claude.enabled
    older = ProviderConfig(name="a", kind="anthropic", model="m")
    assert older.is_paid()


async def test_ordinary_turns_never_use_a_paid_provider(tmp_path):
    router, log = make(tmp_path, [PAID, FREE])
    await ask(router)
    assert log == ["groq"]


async def test_a_paid_provider_answers_only_the_capability_it_was_opted_into(tmp_path):
    router, log = make(tmp_path, [PAID, FREE], CapacityLedger(tmp_path / "c.sqlite", clock=Wall()))
    await ask(router, capability="coding")
    await ask(router, capability="vision")
    assert log == ["claude", "groq"]


async def test_zero_cap_means_never(tmp_path):
    router, log = make(tmp_path, [dict(PAID, monthly_cap_usd=0), FREE], CapacityLedger(tmp_path / "c.sqlite"))
    await ask(router, capability="coding")
    assert log == ["groq"]


async def test_without_a_price_the_spend_cannot_be_checked_so_it_is_not_used(tmp_path):
    router, log = make(tmp_path, [dict(PAID, price_per_mtok_usd=0), FREE], CapacityLedger(tmp_path / "c.sqlite"))
    await ask(router, capability="coding")
    assert log == ["groq"]


async def test_the_monthly_cap_stops_it(tmp_path):
    ledger = CapacityLedger(tmp_path / "c.sqlite", clock=Wall())
    router, log = make(tmp_path, [PAID, FREE], ledger)
    ledger.record("claude", "claude-sonnet-5-5", tokens_in=900_000, tokens_out=0)  # $5.40 at $6 per million
    await ask(router, capability="coding")
    assert log == ["groq"]
    row = next(r for r in router.status() if r["name"] == "claude")
    assert row["paid"] is True and row["spent_usd"] == 5.4 and row["monthly_cap_usd"] == 5.0


async def test_switched_off_paid_provider_is_never_used(tmp_path):
    router, log = make(tmp_path, [dict(PAID, enabled=False), FREE], CapacityLedger(tmp_path / "c.sqlite"))
    await ask(router, capability="coding")
    assert log == ["groq"]


def test_ledger_sums_the_month(tmp_path):
    wall = Wall()
    ledger = CapacityLedger(tmp_path / "c.sqlite", clock=wall)
    ledger.record("claude", "m", 1000, 0)
    wall.now += 86400
    ledger.record("claude", "m", 500, 0)
    assert ledger.month_tokens("claude", "m") == 1500


async def test_without_a_ledger_the_spend_is_unknown_so_it_is_not_used(tmp_path):
    router, log = make(tmp_path, [PAID, FREE])
    await ask(router, capability="coding")
    assert log == ["groq"]
