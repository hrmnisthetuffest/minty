"""Brownout, capacity meter and forecast (W0-20)."""
import calendar

from tests.test_ledger import Wall
from minty_core.brownout import Brownout, capacity_reply
from minty_core.commands import CommandRouter
from minty_core.config import ConfigStore
from minty_core.context import build_context
from minty_core.keystore import SecretsStore
from minty_core.llm.ledger import CapacityLedger
from minty_core.llm.router import ProviderRouter
from minty_core.tui import status_items

NOON_UTC = calendar.timegm((2026, 10, 12, 12, 0, 0))
GROQ = {"name": "groq", "base_url": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b",
        "small_model": "", "limits": {"rpm": 30, "tpm": 8000, "rpd": 1000, "tpd": 200000}}
OPENROUTER = {"name": "openrouter", "base_url": "https://openrouter.ai/api/v1", "model": "x:free",
              "limits": {"rpm": 20, "rpd": 50}}
MISTRAL = {"name": "mistral", "base_url": "https://api.mistral.ai/v1", "model": "m"}


def setup(tmp_path, providers, now=NOON_UTC, **router_cfg):
    store = ConfigStore(tmp_path)
    data = store.load().model_dump(mode="json")
    data["providers"] = providers
    data["router"].update(router_cfg)
    store.update(data)
    secrets = SecretsStore(tmp_path)
    for p in providers:
        secrets.set(p["name"], "key-" + p["name"])
    wall = Wall(now)
    ledger = CapacityLedger(tmp_path / "c.sqlite", clock=wall)
    router = ProviderRouter(store, secrets, ledger=ledger)
    said = []
    return Brownout(router, ledger, store, clock=wall, notify=said.append), ledger, wall, said


def test_capacity_in_calls_across_providers_with_known_daily_limits(tmp_path):
    b, ledger, _w, _s = setup(tmp_path, [GROQ, OPENROUTER, MISTRAL])
    cap = b.capacity()
    # Groq 120b and its small 20b each 200 000 / 6 000 = 33 calls, OpenRouter's free model 50; Mistral unknown
    assert cap.calls_total == 33 + 33 + 50 and cap.calls_left == 116 and cap.share == 1.0
    for _ in range(10):
        ledger.record("openrouter", "x:free", 3000, 100)
    cap = b.capacity()
    assert cap.calls_left == 106 and abs(cap.share - 106 / 116) < 0.01


def test_no_known_limits_means_no_meter(tmp_path):
    b, _l, _w, _s = setup(tmp_path, [MISTRAL])
    cap = b.capacity()
    assert cap.share is None and cap.calls_left is None and not b.active()


def test_forecast(tmp_path):
    b, ledger, wall, _s = setup(tmp_path, [OPENROUTER])
    assert b.capacity().forecast == "lasts until the daily reset"
    for _ in range(40):  # 40 calls in the first 12 hours of the UTC day: 10 left at 3.3 an hour
        ledger.record("openrouter", "x:free", 100, 10)
    forecast = b.capacity().forecast
    assert forecast.startswith("runs out around ") and ":" in forecast


def test_brownout_below_15_percent_says_so_once_a_day(tmp_path):
    b, ledger, wall, said = setup(tmp_path, [OPENROUTER])
    for _ in range(42):
        ledger.record("openrouter", "x:free", 100, 10)
    assert not b.active()
    b.check()
    assert said == []
    for _ in range(2):
        ledger.record("openrouter", "x:free", 100, 10)
    assert b.active() and "short" in b.prompt()
    b.check()
    b.check()
    assert len(said) == 1 and "15" in said[0]
    wall.now += 86400
    assert not b.active() and b.prompt() == ""


def test_brownout_switched_off(tmp_path):
    b, ledger, _w, said = setup(tmp_path, [OPENROUTER], brownout=False)
    for _ in range(49):
        ledger.record("openrouter", "x:free", 100, 10)
    b.check()
    assert not b.active() and said == [] and b.prompt() == ""


def test_how_much_is_left_is_answered_without_a_model(tmp_path):
    b, ledger, _w, _s = setup(tmp_path, [OPENROUTER])
    for _ in range(10):
        ledger.record("openrouter", "x:free", 100, 10)
    router = CommandRouter(agent=None, capacity=b)
    for phrase in ("how much is left", "Minty, how much capacity is left?", "how many calls are left",
                   "how much quota do you have left"):
        reply = router.handle(phrase)
        assert reply and "80 percent" in reply and "40 calls" in reply
    assert CommandRouter(agent=None).handle("how much is left") is None


def test_reply_when_nothing_is_known():
    from minty_core.brownout import Capacity
    assert "can't tell" in capacity_reply(Capacity(None, None, None, ""))


def test_meter_in_the_status_snapshot_and_terminal(tmp_path):
    ctx = build_context(tmp_path)
    from minty_core.statusline import snapshot
    st = snapshot(ctx)
    assert "capacity" in st
    rows = status_items({"capacity": {"share": 0.1, "calls_left": 8, "forecast": "runs out around 15:10"}})
    items = dict((label, value) for label, value, _s in rows)
    assert "10%" in items["CAPACITY"]  # a bar and the share
    assert any("15:10" in value for label, value, _s in rows if label == "")  # running low: the row below says when
    rows = status_items({"capacity": {"share": 0.5, "calls_left": 80, "forecast": "lasts until the daily reset"}})
    assert any("80 calls left" in value for label, value, _s in rows if label == "")


async def test_optional_compaction_waits_during_a_brownout(tmp_path, monkeypatch):
    from minty_core.app import compact_once
    monkeypatch.setattr("minty_core.app.compaction_due", lambda c, now=None: True)  # timing is W0-26's test
    ctx = build_context(tmp_path)
    ran = []

    async def compact(*a, **kw):
        ran.append(1)
        return []

    ctx.services["archive"].compact = compact
    ctx.services["brownout"].active = lambda: True
    await compact_once(ctx)
    assert ran == []
    ctx.services["brownout"].active = lambda: False
    await compact_once(ctx)
    assert ran == [1]


