"""Capacity ledger (W0-18): what each free tier has left today, corrected by the providers' own headers."""
import asyncio

from tests.test_router_hedge import Timed, build as build_hedged
from tests.test_router_health import OK, Queue, fail
from tests.test_router import Clock
from minty_core.config import ConfigStore, Limits, ProviderConfig
from minty_core.keystore import SecretsStore
from minty_core.llm.ledger import CapacityLedger
from minty_core.llm.ratelimit import RateInfo
from minty_core.llm.router import ProviderRouter
from minty_core.llm.types import Message, ToolSpec

DAY = 1_800_000_000.0  # some moment in UTC


class Wall:
    def __init__(self, now=DAY):
        self.now = now

    def __call__(self):
        return self.now


GROQ = ProviderConfig(name="groq", base_url="https://api.groq.com/openai/v1", model="openai/gpt-oss-120b",
                      limits=Limits(rpm=30, tpm=8000, rpd=1000, tpd=200000))
MYSTERY = ProviderConfig(name="mistral", base_url="https://api.mistral.ai/v1", model="mistral-small-latest")


def test_usage_is_counted_per_provider_model_and_day(tmp_path):
    wall = Wall()
    ledger = CapacityLedger(tmp_path / "capacity.sqlite", clock=wall)
    ledger.record("groq", "m", tokens_in=5000, tokens_out=200)
    ledger.record("groq", "m", tokens_in=1000, tokens_out=100)
    ledger.record("groq", "other", tokens_in=7, tokens_out=0)
    assert ledger.used("groq", "m") == {"requests": 2, "tokens": 6300}
    wall.now += 86400
    assert ledger.used("groq", "m") == {"requests": 0, "tokens": 0}


def test_the_ledger_survives_a_restart(tmp_path):
    wall = Wall()
    CapacityLedger(tmp_path / "c.sqlite", clock=wall).record("groq", "m", tokens_in=100, tokens_out=50)
    assert CapacityLedger(tmp_path / "c.sqlite", clock=wall).used("groq", "m") == {"requests": 1, "tokens": 150}


def test_remaining_from_the_daily_limits(tmp_path):
    ledger = CapacityLedger(tmp_path / "c.sqlite", clock=Wall())
    ledger.record("groq", GROQ.model, tokens_in=50000, tokens_out=0)
    left = ledger.remaining(GROQ)
    assert left["requests"] == 999 and left["tokens"] == 150000 and left["share"] == 0.75
    assert ledger.remaining(MYSTERY) == {"requests": None, "tokens": None, "share": None}


def test_the_providers_own_count_wins_when_it_says_less(tmp_path):
    ledger = CapacityLedger(tmp_path / "c.sqlite", clock=Wall())
    ledger.record("groq", GROQ.model, tokens_in=10, tokens_out=0,
                  rate=RateInfo(limit_requests=1000, remaining_requests=12, reset_requests_s=3600))
    assert ledger.remaining(GROQ)["requests"] == 12


def test_a_stale_header_count_is_forgotten_after_its_reset(tmp_path):
    wall = Wall()
    ledger = CapacityLedger(tmp_path / "c.sqlite", clock=wall)
    ledger.record("groq", GROQ.model, tokens_in=10, tokens_out=0,
                  rate=RateInfo(limit_requests=1000, remaining_requests=3, reset_requests_s=60))
    wall.now += 61
    assert ledger.remaining(GROQ)["requests"] == 999


def test_can_afford_checks_the_day_and_the_minute(tmp_path):
    wall = Wall()
    ledger = CapacityLedger(tmp_path / "c.sqlite", clock=wall)
    assert ledger.can_afford(GROQ, 6000)
    ledger.record("groq", GROQ.model, tokens_in=6000, tokens_out=0)
    assert not ledger.can_afford(GROQ, 6000)  # 12 000 in one minute is over 8 000 TPM
    wall.now += 61
    assert ledger.can_afford(GROQ, 6000)
    ledger.record("groq", GROQ.model, tokens_in=195000, tokens_out=0)
    wall.now += 61
    assert not ledger.can_afford(GROQ, 6000)  # the day's 200 000 would be passed
    assert ledger.can_afford(MYSTERY, 10**6)  # unknown limits never block


def test_requests_per_minute(tmp_path):
    cfg = ProviderConfig(name="or", base_url="https://openrouter.ai/api/v1", model="x:free", limits=Limits(rpm=2))
    ledger = CapacityLedger(tmp_path / "c.sqlite", clock=Wall())
    ledger.record("or", "x:free", 1, 1)
    ledger.record("or", "x:free", 1, 1)
    assert not ledger.can_afford(cfg, 1)


def router_with_ledger(tmp_path, scripts, ledger):
    store = ConfigStore(tmp_path)
    data = store.load().model_dump(mode="json")
    data["providers"] = [{"name": n, "model": "m"} for n in scripts]
    store.update(data)
    secrets = SecretsStore(tmp_path)
    for n in scripts:
        secrets.set(n, "key-" + n)
    return ProviderRouter(store, secrets, lambda cfg, key: Queue(cfg.name, scripts[cfg.name]), Clock(), ledger=ledger)


async def test_the_router_records_every_accepted_call(tmp_path):
    ledger = CapacityLedger(tmp_path / "c.sqlite", clock=Wall())
    router = router_with_ledger(tmp_path, {"a": [fail(429)], "b": [OK]}, ledger)
    tools = [ToolSpec("clock_get_time", "Tell the time", {"type": "object"})]
    [_ async for _ in router.stream([Message("system", "x" * 400), Message("user", "y" * 400)], tools)]
    assert ledger.used("a", "m")["requests"] == 0  # a 429 was refused, not served
    used = ledger.used("b", "m")
    assert used["requests"] == 1 and 200 <= used["tokens"] <= 260  # ~4 characters a token, tools included


async def test_both_hedged_calls_are_counted(tmp_path):
    a, b = Timed("a", [(0.5, "A")]), Timed("b", [(0.0, "B")])
    router, _ = build_hedged(tmp_path, {"a": a, "b": b})
    router.ledger = CapacityLedger(tmp_path / "c.sqlite", clock=Wall())
    [_ async for _ in router.stream([Message("user", "hi " * 100)], [])]
    await asyncio.sleep(0)
    assert router.ledger.used("a", "m")["requests"] == 1 and router.ledger.used("b", "m")["requests"] == 1


async def test_ledger_switched_off_records_nothing(tmp_path):
    ledger = CapacityLedger(tmp_path / "c.sqlite", clock=Wall())
    router = router_with_ledger(tmp_path, {"b": [OK]}, ledger)
    data = router._store.config.model_dump(mode="json")
    data["router"]["ledger"] = False
    router._store.update(data)
    [_ async for _ in router.stream([Message("user", "hi")], [])]
    assert ledger.used("b", "m")["requests"] == 0
