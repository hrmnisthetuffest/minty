"""The finance tools: read-only, totals and categories for a model, rows for you only (LF-11..LF-14)."""
import ast
import re
from datetime import date
from pathlib import Path

from tests.plugin_helpers import make_host
from minty_core.finance import Ledger
from minty_core.finance_import import parse_quick

SRC = Path(__file__).resolve().parents[1] / "src" / "minty_core"
FINANCE_FILES = [SRC / "finance.py", SRC / "finance_import.py", SRC / "finance_insights.py", SRC / "finance_pack.py",
                 SRC / "finance_commands.py", SRC / "scamcheck.py",
                 SRC / "plugins" / "builtin" / "finance" / "plugin.py", SRC / "telegram" / "finance.py"]
FORBIDDEN_IMPORTS = {"httpx", "requests", "urllib", "socket", "aiohttp", "smtplib", "subprocess", "webbrowser",
                     "http", "ftplib", "ssl", "asyncio.subprocess"}
FORBIDDEN_NAMES = re.compile(r"(^|_)(pay|payment|transfer|remit|send_money|trade|upi_pay|initiate|buy|sell)(_|$)", re.I)


def host(tmp_path, enabled=True):
    ledger = Ledger(tmp_path / "finance.db", clock=lambda: 1_800_000_000.0)
    plugin = make_host(tmp_path / "cfg", services={"finance": ledger})
    data = plugin._store.config.model_dump(mode="json")
    data["finance"]["enabled"] = enabled
    plugin._store.update(data)
    return plugin, ledger


def test_no_finance_code_can_reach_the_network_or_start_a_payment():
    seen = 0
    for path in FINANCE_FILES:
        assert path.is_file(), path
        tree = ast.parse(path.read_text(encoding="utf-8"))
        seen += 1
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                names = []
            for n in names:
                assert n.split(".")[0] not in FORBIDDEN_IMPORTS, f"{path.name} imports {n}"
            ident = (node.name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                     else node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name)
                     else "")
            assert not FORBIDDEN_NAMES.search(ident), f"{path.name}: {ident}"
    assert seen == len(FINANCE_FILES)


async def test_the_tool_list_has_no_way_to_move_money(tmp_path):
    plugin, _ = host(tmp_path)
    names = [s.name for s in plugin.tool_specs() if s.name.startswith("finance_")]
    assert names and not [n for n in names if FORBIDDEN_NAMES.search(n[len("finance_"):])]
    assert "finance_delete_data" in names


async def test_it_is_off_until_switched_on(tmp_path):
    plugin, _ = host(tmp_path, enabled=False)
    assert "switched off" in await plugin.call("finance_summary", {})


async def test_a_spoken_spend_is_added_and_categorised(tmp_path):
    plugin, ledger = host(tmp_path)
    out = await plugin.call("finance_add", {"text": "spent 250 on zomato lunch"})
    assert "Added" in out and "2.50" not in out and "250.00" in out and "food" in out
    assert ledger.transactions()[0]["amount_minor"] == -25000
    assert (await plugin.call("finance_add", {"text": "what time is it"})).startswith("Error")


async def test_a_correction_teaches_a_merchant(tmp_path):
    plugin, ledger = host(tmp_path)
    acct = ledger.add_account("Cash")
    ledger.add_batch(acct, [("2026-10-01", -30000, "UPI/RAVI KIRANA/1"), ("2026-10-09", -21000, "UPI/RAVI KIRANA/2")])
    out = await plugin.call("finance_correct", {"match": "ravi kirana", "category": "groceries"})
    assert "2 transactions" in out and {t["category"] for t in ledger.transactions()} == {"groceries"}
    assert "no transaction" in (await plugin.call("finance_correct", {"match": "zzz", "category": "food"})).lower()


async def test_setting_budgets_goals_and_bills(tmp_path):
    plugin, ledger = host(tmp_path)
    assert "5000.00" in await plugin.call("finance_set", {"kind": "budget", "name": "food", "amount": "5000"})
    assert "Savings goal" in await plugin.call("finance_set", {"kind": "goal", "name": "Laptop", "amount": "40000",
                                                               "due": "2027-03-20"})
    assert "due on day 22" in await plugin.call("finance_set", {"kind": "bill", "name": "Electricity",
                                                                "amount": "900", "due_day": 22})
    assert ledger.budgets() == {"food": 500000} and ledger.bills()[0]["due_day"] == 22
    assert "added" in (await plugin.call("finance_set", {"kind": "contribute", "name": "Laptop",
                                                         "amount": "1000"})).lower()


async def test_the_summary_for_a_model_has_no_merchants_and_show_is_for_you_only(tmp_path):
    plugin, ledger = host(tmp_path)
    acct = ledger.add_account("HDFC savings 50100123456789")
    ledger.add_batch(acct, [("2026-10-05", -180000, "ZOMATO ORDER 4412"), ("2026-10-06", -50000, "UPI/RAVI KIRANA")])
    summary = await plugin.call("finance_summary", {"month": "2026-10"})
    for leak in ("ZOMATO", "RAVI", "KIRANA", "4412", "50100123456789", "HDFC"):
        assert leak not in summary
    assert "food" in summary and "1800.00" in summary
    show = plugin.get_tool("finance_show")
    assert show.local_only is True and show.sensitive is True
    rows = await plugin.call("finance_show", {"what": "transactions", "month": "2026-10"})
    assert "ZOMATO" in rows  # for your eyes: this result is never given to a model


async def test_delete_is_class_three_and_export_writes_a_local_file(tmp_path):
    plugin, ledger = host(tmp_path)
    acct = ledger.add_account("Cash")
    ledger.add_batch(acct, [("2026-10-05", -180000, "ZOMATO")])
    assert plugin.get_tool("finance_delete_data").risk == 3
    out = await plugin.call("finance_export", {"format": "csv"})
    path = Path(re.search(r"(/\S+\.csv)", out).group(1))
    assert path.is_file() and "ZOMATO" in path.read_text() and oct(path.stat().st_mode & 0o777) == "0o600"
    assert "Deleted 1" in await plugin.call("finance_delete_data", {})


async def test_insight_and_scam_tools(tmp_path):
    plugin, ledger = host(tmp_path)
    acct = ledger.add_account("Main")
    ledger.add_batch(acct, [("2026-10-01", -30000, "NETFLIX.COM A"), ("2026-09-01", -30000, "NETFLIX.COM A"),
                            ("2026-08-01", -30000, "NETFLIX.COM A")])
    assert "recurring charge" in await plugin.call("finance_show", {"what": "audit"})  # names: for you only
    assert (await plugin.call("finance_insight", {"what": "audit"})).startswith("Error")  # not model-visible
    assert "Tell me" in await plugin.call("finance_insight", {"what": "forecast"})
    assert "balance" in (await plugin.call("finance_insight", {"what": "safe"})).lower()
    scam = await plugin.call("finance_scam", {"text": "Share your OTP now or your account will be blocked today"})
    assert "scam" in scam.lower() and "not a guarantee" in scam.lower()
    assert plugin.get_tool("finance_scam").local_only is False
    data = plugin._store.config.model_dump(mode="json")
    data["finance"]["insights"] = False
    plugin._store.update(data)
    assert "switched off" in await plugin.call("finance_insight", {"what": "forecast"})
