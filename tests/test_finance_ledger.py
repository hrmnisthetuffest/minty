"""The finance ledger: integer paise, no duplicates, rules learned from corrections, exports (LF-11, LF-13)."""
import csv
import io
import stat

import pytest

from minty_core.finance import Ledger, categorize_builtin, merchant_key, parse_money


def ledger(tmp_path):
    return Ledger(tmp_path / "finance.db", clock=lambda: 1_800_000_000.0)


def test_money_is_parsed_into_paise_without_floats():
    assert parse_money("1,234.50") == 123450
    assert parse_money("Rs. 200") == 20000 and parse_money("₹99.9") == 9990
    assert parse_money("(450.00)") == -45000 and parse_money("-12.05") == -1205
    assert parse_money("0.07") == 7
    for bad in ("", "abc", "12.345.6", "1e5"):
        with pytest.raises(ValueError):
            parse_money(bad)


def test_the_merchant_key_drops_references_and_bank_noise():
    assert merchant_key("UPI/DR/412345678901/ZOMATO/okaxis/Payment") == "ZOMATO"
    assert merchant_key("POS 4521 SWIGGY INSTAMART BANGALORE") == "SWIGGY INSTAMART"
    assert merchant_key("NEFT-HDFC0000123-RAVI KIRANA STORE-REF99") == "RAVI KIRANA"
    assert merchant_key("1234 5678") == ""


def test_builtin_categories_cover_the_usual_merchants():
    for text, want in [("ZOMATO ORDER", "food"), ("Uber trip", "transport"), ("BIGBASKET", "groceries"),
                       ("NETFLIX.COM", "entertainment"), ("School fees Q2", "education"),
                       ("Jio recharge", "bills"), ("Salary Oct", "income"), ("XYZZY TRADERS", "")]:
        assert categorize_builtin(text) == want


def test_a_batch_added_twice_is_added_once_and_two_equal_coffees_are_two(tmp_path):
    led = ledger(tmp_path)
    acct = led.add_account("HDFC savings")
    rows = [("2026-10-01", -12000, "UPI/ZOMATO/1"), ("2026-10-02", -4500, "COFFEE HOUSE"),
            ("2026-10-02", -4500, "COFFEE HOUSE")]
    first = led.add_batch(acct, rows, source="csv")
    again = led.add_batch(acct, rows, source="csv")
    assert (first.added, first.duplicates) == (3, 0) and (again.added, again.duplicates) == (0, 3)
    assert len(led.transactions()) == 3


def test_a_correction_teaches_the_merchant_and_fixes_older_rows(tmp_path):
    led = ledger(tmp_path)
    acct = led.add_account("Cash")
    led.add_batch(acct, [("2026-09-03", -25000, "UPI/RAVI KIRANA STORE/123"),
                         ("2026-09-20", -18000, "UPI/RAVI KIRANA STORE/456"),
                         ("2026-10-02", -9000, "ZOMATO ORDER")], source="csv")
    txn = next(t for t in led.transactions() if "RAVI" in t["description"])
    changed = led.set_category(txn["id"], "groceries")
    assert changed == 2  # this row and the other Ravi Kirana row
    assert {t["category"] for t in led.transactions() if "RAVI" in t["description"]} == {"groceries"}
    led.add_batch(acct, [("2026-10-05", -11000, "UPI/RAVI KIRANA STORE/789")], source="csv")
    assert led.transactions(limit=1)[0]["category"] == "groceries"  # a new row is categorised by the rule


def test_a_correction_never_overwrites_a_category_you_set_by_hand(tmp_path):
    led = ledger(tmp_path)
    acct = led.add_account("Cash")
    led.add_batch(acct, [("2026-09-03", -25000, "UPI/RAVI KIRANA STORE/1"),
                         ("2026-09-04", -25000, "UPI/RAVI KIRANA STORE/2")], source="csv")
    a, b = led.transactions()
    led.set_category(a["id"], "groceries")
    led.set_category(b["id"], "household", learn=False)  # a one-off, not a rule
    led.set_category(a["id"], "groceries")
    assert {t["id"]: t["category"] for t in led.transactions()}[b["id"]] == "household"


def test_exports_round_trip_through_csv_and_look_like_beancount(tmp_path):
    led = ledger(tmp_path)
    acct = led.add_account("HDFC savings")
    led.add_batch(acct, [("2026-10-01", -12000, "ZOMATO ORDER"), ("2026-10-02", 5000000, "SALARY OCT")],
                  source="csv")
    rows = list(csv.DictReader(io.StringIO(led.export_csv())))
    assert [r["amount"] for r in rows] == ["-120.00", "50000.00"] and rows[0]["category"] == "food"
    bean = led.export_beancount()
    assert "2026-10-01 * \"ZOMATO ORDER\"" in bean and "Expenses:Food  120.00 INR" in bean
    assert "Assets:HDFC-savings  -120.00 INR" in bean or "Assets:HDFC-savings -120.00 INR" in bean


def test_the_file_is_owner_only_and_amounts_are_integers(tmp_path):
    led = ledger(tmp_path)
    assert stat.S_IMODE((tmp_path / "finance.db").stat().st_mode) == 0o600
    acct = led.add_account("Cash")
    led.add_batch(acct, [("2026-10-01", -1999, "X")], source="manual")
    assert isinstance(led.transactions()[0]["amount_minor"], int)
