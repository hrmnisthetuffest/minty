"""Budgets, goals, subscriptions, bills, the monthly report and insights (LF-13, LF-14)."""
import random
from datetime import date, timedelta

from minty_core.finance import Ledger, money
from minty_core.finance_insights import (afford, budget_status, detect_recurring, due_bills, facts_pack,
                                         monthly_report, unusual)

TODAY = date(2026, 10, 20)


def led(tmp_path):
    ledger = Ledger(tmp_path / "f.db", clock=lambda: 1_800_000_000.0)
    return ledger, ledger.add_account("Main")


def add(ledger, acct, day, minor, desc, cat=""):
    ledger.add_manual(acct, day.isoformat() if hasattr(day, "isoformat") else day, minor, desc, cat)


def test_an_envelope_budget_shows_spent_left_and_where_it_is_heading(tmp_path):
    ledger, acct = led(tmp_path)
    ledger.set_budget("food", 500000)  # Rs 5 000
    for d, amt in ((2, 80000), (6, 120000), (11, 90000), (15, 110000)):
        add(ledger, acct, date(2026, 10, d), -amt, "ZOMATO", "food")
    add(ledger, acct, date(2026, 10, 12), -300000, "SALARY SHARE", "income")  # income is not spending
    line = next(b for b in budget_status(ledger, "2026-10", TODAY) if b.category == "food")
    assert line.spent_minor == 400000 and line.remaining_minor == 100000 and line.pct == 80
    assert line.status == "heading over"  # Rs 4 000 in 20 of 31 days projects to about Rs 6 200
    assert line.projected_minor == 620000


def test_going_over_is_called_over(tmp_path):
    ledger, acct = led(tmp_path)
    ledger.set_budget("shopping", 100000)
    add(ledger, acct, date(2026, 10, 3), -150000, "AMAZON", "shopping")
    assert budget_status(ledger, "2026-10", TODAY)[0].status == "over"


def test_a_savings_goal_tracks_progress_and_what_a_month_must_add(tmp_path):
    ledger, acct = led(tmp_path)
    gid = ledger.add_goal("New laptop", 4000000, due="2027-03-20")
    ledger.contribute(gid, 1000000)
    goal = ledger.goals(TODAY)[0]
    assert goal["pct"] == 25 and goal["saved_minor"] == 1000000
    assert goal["monthly_needed_minor"] == 600000  # Rs 30 000 left over exactly 5 months


def test_seeded_subscriptions_are_found_with_at_most_one_false_positive_in_fifty(tmp_path):
    ledger, acct = led(tmp_path)
    rng = random.Random(7)
    seeded = {"NETFLIX.COM": 64900, "SPOTIFY INDIA": 11900, "CULT FIT GYM": 150000, "JIO RECHARGE": 29900}
    for name, amount in seeded.items():
        for m in range(6):
            day = date(2026, 5, 3) + timedelta(days=30 * m + rng.randint(-1, 1))
            add(ledger, acct, day, -amount, name)
    merchants = ["ZOMATO", "SWIGGY", "BIGBASKET", "UBER", "AMAZON", "MYNTRA", "KIRANA RAVI", "CAFE COFFEE DAY",
                 "PHARMACY APOLLO", "BOOK DEPOT", "RANDOM SHOP", "BLINKIT"]
    for _ in range(50):  # fifty ordinary, irregular purchases
        add(ledger, acct, date(2026, 5, 1) + timedelta(days=rng.randint(0, 170)), -rng.randint(5000, 250000),
            rng.choice(merchants))
    found = detect_recurring(ledger.transactions())
    names = {r.merchant for r in found}
    assert {"NETFLIX COM", "SPOTIFY INDIA", "CULT FIT", "JIO RECHARGE"} <= names
    assert len(names - {"NETFLIX COM", "SPOTIFY INDIA", "CULT FIT", "JIO RECHARGE"}) <= 1
    netflix = next(r for r in found if r.merchant == "NETFLIX COM")
    assert netflix.typical_minor == 64900 and 28 <= netflix.interval_days <= 32 and netflix.count == 6


def test_two_equal_charges_are_not_a_subscription(tmp_path):
    ledger, acct = led(tmp_path)
    add(ledger, acct, date(2026, 8, 1), -50000, "ONE OFF SHOP")
    add(ledger, acct, date(2026, 9, 1), -50000, "ONE OFF SHOP")
    assert detect_recurring(ledger.transactions()) == []


def test_bills_due_soon_and_not_already_paid_this_month(tmp_path):
    ledger, acct = led(tmp_path)
    ledger.add_bill("Electricity", 90000, due_day=22)
    ledger.add_bill("Broadband", 59900, due_day=5)
    ledger.add_bill("School fees", 250000, due_day=28)
    add(ledger, acct, date(2026, 10, 4), -59900, "BROADBAND AIRTEL")
    due = due_bills(ledger, TODAY, within_days=3)
    assert [b["name"] for b in due] == ["Electricity"] and due[0]["due"] == "2026-10-22"
    assert [b["name"] for b in due_bills(ledger, TODAY, within_days=10)] == ["Electricity", "School fees"]


def test_the_monthly_report_is_deterministic_and_names_the_changes(tmp_path):
    ledger, acct = led(tmp_path)
    add(ledger, acct, date(2026, 9, 5), -100000, "ZOMATO", "food")
    add(ledger, acct, date(2026, 10, 5), -180000, "ZOMATO", "food")
    add(ledger, acct, date(2026, 10, 6), -50000, "UBER", "transport")
    add(ledger, acct, date(2026, 10, 1), 2000000, "POCKET MONEY", "income")
    r = monthly_report(ledger, "2026-10")
    assert r.income_minor == 2000000 and r.spent_minor == 230000 and r.net_minor == 1770000
    assert "food" in r.text and "up 80%" in r.text and "ZOMATO" in r.text  # shown to you, not to a model


def test_the_facts_pack_has_totals_and_categories_only(tmp_path):
    ledger, acct = led(tmp_path)
    add(ledger, acct, date(2026, 10, 5), -180000, "ZOMATO ORDER 4412", "food")
    add(ledger, acct, date(2026, 10, 6), -50000, "UPI/RAVI KIRANA", "groceries")
    ledger.set_budget("food", 150000)
    pack = facts_pack(ledger, "2026-10", TODAY)
    assert "food" in pack and "groceries" in pack and "1800" in pack
    for leak in ("ZOMATO", "RAVI", "KIRANA", "4412", "UPI"):
        assert leak not in pack


def test_an_unusually_large_purchase_is_flagged_and_afford_uses_your_own_envelope(tmp_path):
    ledger, acct = led(tmp_path)
    for i in range(6):
        add(ledger, acct, date(2026, 9, 1) + timedelta(days=3 * i), -20000 - i * 500, "CAFE", "food")
    add(ledger, acct, date(2026, 10, 10), -150000, "FANCY DINNER", "food")
    flagged = unusual(ledger.transactions(), TODAY)
    assert [t["description"] for t in flagged] == ["FANCY DINNER"]
    ledger.set_budget("food", 200000)
    answer = afford(ledger, 100000, "food", TODAY)
    assert answer.ok is False and "not financial advice" in answer.text.lower()
    assert money(answer.remaining_minor) in answer.text
