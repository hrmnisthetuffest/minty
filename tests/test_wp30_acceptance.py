"""WP-30 acceptance: forecast, recurring detection, scam check, no payment path, and what a model can see."""
from datetime import date, timedelta

from tests.plugin_helpers import make_host
import tests.test_finance_pack as pack_tests
import tests.test_finance_plugin as plugin_tests
import tests.test_scamcheck as scam_tests
from minty_core.agent import Agent
from minty_core.config import ConfigStore
from minty_core.finance import Ledger
from minty_core.keystore import SecretsStore
from minty_core.llm.router import ProviderRouter
from minty_core.llm.types import TextDelta, ToolCall, ToolCallEvent
from minty_core.memory import MemoryStore
from minty_core.personality import PersonalityStore
from minty_core.scamcheck import check

TODAY = date(2026, 10, 20)


def test_the_forecast_matches_hand_computed_values(tmp_path):
    pack_tests.test_the_forecast_matches_a_hand_worked_month(tmp_path)


def test_recurring_detection_has_at_most_one_false_positive_in_fifty(tmp_path):
    from tests.test_finance_insights import test_seeded_subscriptions_are_found_with_at_most_one_false_positive_in_fifty
    test_seeded_subscriptions_are_found_with_at_most_one_false_positive_in_fifty(tmp_path)


def test_the_scam_check_flags_all_twenty_scams_and_at_most_two_genuine_alerts():
    flagged = [m for m in scam_tests.SCAMS if check(m).verdict != "no red flags found"]
    assert len(flagged) == 20
    wrong = [m for m in scam_tests.GENUINE if check(m).verdict != "no red flags found"]
    assert len(wrong) <= 2


def test_no_code_path_in_the_pack_can_initiate_a_payment_or_transfer():
    names = {p.name for p in plugin_tests.FINANCE_FILES}
    assert {"finance_pack.py", "scamcheck.py", "finance_commands.py"} <= names
    plugin_tests.test_no_finance_code_can_reach_the_network_or_start_a_payment()


class Recording:
    def __init__(self, log, name, script):
        self.log, self.name, self.script = log, name, script

    async def stream(self, messages, tools):
        self.log.append((self.name, "\n".join(f"{m.role}: {m.content}" for m in messages)))
        for ev in self.script.pop(0) if self.script else [TextDelta("Noted.")]:
            yield ev


async def test_the_money_insight_tools_give_a_model_no_merchant_or_bill_names(tmp_path):
    store = ConfigStore(tmp_path)
    store.load()
    data = store.config.model_dump(mode="json")
    data["toolkit_routing"] = False
    data["finance"]["enabled"] = True
    store.update(data)
    secrets = SecretsStore(tmp_path)
    for cfg in store.config.providers:
        if cfg.requires_key:
            secrets.set(cfg.key_name or cfg.name, "key-" + cfg.name)
    ledger = Ledger(tmp_path / "f.db", clock=lambda: 1_792_000_000.0)  # 2026-10-20
    acct = ledger.add_account("HDFC 50100123456789")
    for k in range(5):
        ledger.add_manual(acct, (date(2026, 9, 5) - timedelta(days=30 * k)).isoformat(), -64900, "NETFLIX.COM", "entertainment")
    ledger.add_bill("Electricity BESCOM", 90000, due_day=24)
    ledger.set_budget("entertainment", 100000)
    seen: list[tuple[str, str]] = []
    script = [[ToolCallEvent(ToolCall("1", "finance_insight", {"what": "forecast", "balance": "20000"}))],
              [TextDelta("About 15,000 rupees left by month end.")],
              [ToolCallEvent(ToolCall("2", "finance_show", {"what": "audit"}))]]
    router = ProviderRouter(store, secrets, lambda cfg, key: Recording(seen, cfg.name, script))
    host = make_host(tmp_path / "cfg", services={"finance": ledger})
    hdata = host._store.config.model_dump(mode="json")
    hdata["finance"]["enabled"] = True
    host._store.update(hdata)
    personalities = PersonalityStore(tmp_path)
    personalities.ensure_defaults()

    async def confirm(prompt):
        return True

    agent = Agent(router, host, store, personalities, MemoryStore(tmp_path / "m.md"), confirm)
    [s async for s in agent.respond("what will my money look like this month")]
    shown = "".join([s async for s in agent.respond("list my subscriptions")])
    assert "NETFLIX" in shown  # you see the names
    for _, prompt in seen:
        for leak in ("NETFLIX", "BESCOM", "Electricity", "50100123456789", "HDFC"):
            assert leak not in prompt, leak
