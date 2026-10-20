"""WP-24 acceptance: a 200-row statement, no payment path, a model sees totals only, recurring charges."""
import random
from datetime import date, timedelta

from tests.plugin_helpers import make_host
import tests.test_finance_plugin as plugin_tests
from minty_core.agent import Agent
from minty_core.config import ConfigStore
from minty_core.finance import Ledger
from minty_core.finance_import import import_csv
from minty_core.finance_insights import detect_recurring
from minty_core.keystore import SecretsStore
from minty_core.llm.router import ProviderRouter
from minty_core.llm.types import TextDelta, ToolCall, ToolCallEvent
from minty_core.memory import MemoryStore
from minty_core.personality import PersonalityStore

# merchant text as a bank prints it -> the category you would call it
KNOWN = {"UPI/ZOMATO/{n}/okaxis": "food", "UPI/SWIGGY/{n}/ybl": "food", "POS BIGBASKET {n}": "groceries",
         "UPI/UBER INDIA/{n}": "transport", "AMAZON PAY {n}": "shopping", "NETFLIX.COM {n}": "entertainment",
         "JIO RECHARGE {n}": "bills", "APOLLO PHARMACY {n}": "health", "BOOKMYSHOW {n}": "entertainment"}
UNKNOWN = {"UPI/RAVI KIRANA STORE/{n}": "groceries", "NEFT-GURU NANAK STATIONERS-{n}": "education",
           "POS COOLBAR LYON {n}": "food"}


def statement(seed=3):
    rng = random.Random(seed)
    rows, truth = [], []
    templates = list(KNOWN.items()) * 15 + list(UNKNOWN.items()) * 17  # 135 + 51 = 186, then 14 more known
    templates += list(KNOWN.items())[:5] * 2 + list(KNOWN.items())[5:9]
    rng.shuffle(templates)
    for i, (tpl, cat) in enumerate(templates[:200]):
        d = date(2026, 7, 1) + timedelta(days=i % 90)
        amt = rng.randint(4000, 180000) / 100
        rows.append(f"{d.strftime('%d/%m/%Y')},{tpl.format(n=100000 + i)},-{amt:.2f},0.00")
        truth.append(cat)
    return "Date,Narration,Amount,Balance\n" + "\n".join(rows) + "\n", truth


def accuracy(ledger, truth):
    got = [t["category"] for t in sorted(ledger.transactions(), key=lambda t: t["id"])]
    return sum(1 for g, w in zip(got, truth) if g == w) / len(truth)


def test_a_two_hundred_row_statement_reaches_95_percent_after_three_corrections_and_reimports_clean(tmp_path):
    ledger = Ledger(tmp_path / "f.db", clock=lambda: 1_800_000_000.0)
    acct = ledger.add_account("HDFC savings")
    data, truth = statement()
    first = import_csv(ledger, acct, data.encode())
    assert first.added == 200 and first.problems == 0
    # order of ids follows the file, so truth lines up with the rows
    before = accuracy(ledger, truth)
    assert before < 0.95  # the built-in map alone is not enough
    for needle in ("RAVI KIRANA", "GURU NANAK", "COOLBAR"):
        row = next(t for t in ledger.transactions() if needle in t["description"])
        want = {"RAVI KIRANA": "groceries", "GURU NANAK": "education", "COOLBAR": "food"}[needle]
        ledger.set_category(row["id"], want)  # three corrections
    after = accuracy(ledger, truth)
    assert after >= 0.95, (before, after)
    again = import_csv(ledger, acct, data.encode())
    assert again.added == 0 and again.duplicates == 200 and ledger.count() == 200


def test_no_code_path_can_initiate_a_payment_or_transfer():
    assert len(plugin_tests.FINANCE_FILES) == 8
    plugin_tests.test_no_finance_code_can_reach_the_network_or_start_a_payment()


class Recording:
    def __init__(self, log, name, script):
        self.log, self.name, self.script = log, name, script

    async def stream(self, messages, tools):
        self.log.append((self.name, "\n".join(f"{m.role}: {m.content}" for m in messages)))
        for ev in self.script.pop(0) if self.script else [TextDelta("Noted.")]:
            yield ev


async def test_a_cloud_prompt_built_from_the_ledger_has_totals_and_categories_only(tmp_path):
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
    ledger = Ledger(tmp_path / "finance.db", clock=lambda: 1_800_000_000.0)
    acct = ledger.add_account("HDFC savings 50100123456789")
    ledger.add_batch(acct, [("2026-10-05", -180000, "UPI/ZOMATO/412345678901/okaxis"),
                            ("2026-10-06", -50000, "UPI/RAVI KIRANA STORE/777"),
                            ("2026-10-07", -64900, "NETFLIX.COM 8812")])
    ledger.set_budget("food", 150000)
    seen: list[tuple[str, str]] = []
    script = [[ToolCallEvent(ToolCall("1", "finance_summary", {"month": "2026-10"}))],
              [TextDelta("You spent most on food this month.")]]
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
    out = "".join([s async for s in agent.respond("how much did I spend this month")])
    assert out == "You spent most on food this month."
    summary_prompts = [p for _, p in seen if "tool:" in p]
    assert summary_prompts, "the model never saw the summary"
    for _, prompt in seen:
        for leak in ("ZOMATO", "RAVI", "KIRANA", "NETFLIX", "412345678901", "50100123456789", "HDFC", "8812"):
            assert leak not in prompt, leak
    assert "food" in summary_prompts[0] and "1800.00" in summary_prompts[0]


def test_seeded_recurring_charges_are_found_from_a_statement_with_at_most_one_false_positive(tmp_path):
    rng = random.Random(11)
    ledger = Ledger(tmp_path / "f.db", clock=lambda: 1_800_000_000.0)
    acct = ledger.add_account("Main")
    lines = []
    for name, amount in (("NETFLIX.COM", 649.00), ("SPOTIFY INDIA", 119.00), ("CULT FIT GYM", 1500.00)):
        for m in range(6):
            d = date(2026, 5, 3) + timedelta(days=30 * m + rng.randint(-1, 1))
            lines.append(f"{d.strftime('%d/%m/%Y')},{name},-{amount:.2f}")
    merchants = ["ZOMATO", "SWIGGY", "BIGBASKET", "UBER", "AMAZON", "MYNTRA", "KIRANA RAVI", "BLINKIT", "RANDOM SHOP",
                 "BOOK DEPOT"]
    for _ in range(50):
        d = date(2026, 5, 1) + timedelta(days=rng.randint(0, 170))
        lines.append(f"{d.strftime('%d/%m/%Y')},{rng.choice(merchants)},-{rng.randint(50, 2500)}.00")
    import_csv(ledger, acct, ("Date,Narration,Amount\n" + "\n".join(lines) + "\n").encode())
    found = {r.merchant for r in detect_recurring(ledger.transactions())}
    want = {"NETFLIX COM", "SPOTIFY INDIA", "CULT FIT"}
    assert want <= found and len(found - want) <= 1
