"""Budget alerts, subscription audit, bill calendar, forecast, safe to spend, allowance, values, rules (WP-30)."""
from datetime import date, datetime, timedelta

import pytest

from minty_core.finance import Ledger
from minty_core.finance_pack import (budget_alerts, bill_calendar, cash_flow_forecast, daily_allowance,
                                     membership_value, safe_to_spend, subscription_audit, subscription_value)
from minty_core.health import HealthHub

TODAY = date(2026, 10, 20)


def led(tmp_path):
    ledger = Ledger(tmp_path / "f.db", clock=lambda: 1_800_000_000.0)
    return ledger, ledger.add_account("Main")


def add(ledger, acct, d, minor, desc, cat=""):
    ledger.add_manual(acct, d.isoformat(), minor, desc, cat)


def monthly(ledger, acct, desc, minor, day_of_month, months=5, cat="", last_month=10):
    for k in range(months):
        m = last_month - k
        add(ledger, acct, date(2026, m, day_of_month), -minor, desc, cat)


def test_budget_alerts_fire_at_80_and_100_and_when_heading_over(tmp_path):
    ledger, acct = led(tmp_path)
    ledger.set_budget("food", 500000)
    add(ledger, acct, date(2026, 10, 3), -410000, "ZOMATO", "food")  # 82 %
    alerts = budget_alerts(ledger, "2026-10", TODAY)
    assert [(a.category, a.threshold) for a in alerts] == [("food", 80)]
    add(ledger, acct, date(2026, 10, 10), -100000, "SWIGGY", "food")  # 102 %
    assert [(a.category, a.threshold) for a in budget_alerts(ledger, "2026-10", TODAY)] == [("food", 100)]
    ledger.set_budget("shopping", 600000)
    add(ledger, acct, date(2026, 10, 2), -400000, "AMAZON", "shopping")  # 67 %, Rs 4 000 in 20 of 31 days
    heading = [a for a in budget_alerts(ledger, "2026-10", TODAY) if a.category == "shopping"]
    assert heading and heading[0].threshold == 0 and "heading over" in heading[0].text.lower()


def test_subscription_audit_totals_the_year_and_flags_overlap_and_the_quiet_ones(tmp_path):
    ledger, acct = led(tmp_path)
    monthly(ledger, acct, "NETFLIX.COM", 64900, 5, 6, "entertainment")
    monthly(ledger, acct, "HOTSTAR", 29900, 8, 6, "entertainment")
    monthly(ledger, acct, "CULT FIT GYM", 150000, 12, 5, "health")
    for k in range(3):  # a subscription that stopped: last charged in July
        add(ledger, acct, date(2026, 5 + k, 2), -11900, "SPOTIFY INDIA", "entertainment")
    audit = subscription_audit(ledger, TODAY)
    assert audit.yearly_minor == (64900 + 29900 + 150000) * 12  # the stopped one is not counted
    assert any("two" in o.lower() and "entertainment" in o.lower() for o in audit.overlaps)
    assert [q.merchant for q in audit.quiet] == ["SPOTIFY INDIA"] and audit.quiet[0].days_since >= 60


def test_the_bill_calendar_merges_your_bills_and_detected_charges_in_date_order(tmp_path):
    ledger, acct = led(tmp_path)
    ledger.add_bill("Electricity", 90000, due_day=24)
    monthly(ledger, acct, "NETFLIX.COM", 64900, 28, 5, "entertainment", last_month=9)
    items = bill_calendar(ledger, TODAY, days=14)
    assert [(i.day, i.name) for i in items] == [("2026-10-24", "Electricity"), ("2026-10-28", "NETFLIX COM")]
    assert items[0].source == "bill" and items[1].source == "recurring" and items[1].minor == 64900


def test_the_forecast_matches_a_hand_worked_month(tmp_path):
    ledger, acct = led(tmp_path)
    monthly(ledger, acct, "SALARY", -5000000, 1, 5, "income")  # income: amount is +50 000 (monthly() negates)
    monthly(ledger, acct, "NETFLIX.COM", 64900, 25, 5, "entertainment", last_month=9)
    ledger.add_bill("Rent", 1500000, due_day=3)
    # 10 days of ordinary spending at Rs 800 a day in the last 30 days (unrelated to the recurring charges)
    for i in range(1, 11):
        add(ledger, acct, TODAY - timedelta(days=i), -80000, f"SHOP {i}")
    f = cash_flow_forecast(ledger, TODAY, balance_minor=3000000, days=30)
    # outflows: Netflix 649 (25 Oct) + Rent 15 000 (3 Nov) + 30 x daily spend; inflow: salary 50 000 (1 Nov)
    daily = 80000 * 10 / 30  # Rs 800 over 10 of the last 30 days = Rs 266.67 a day
    expected_end = 3000000 - 64900 - 1500000 + 5000000 - round(daily * 30)
    assert f.end_balance_minor == pytest.approx(expected_end, abs=100)
    assert f.low_day == "2026-10-31"  # just before the salary lands
    assert f.low_minor == pytest.approx(3000000 - 64900 - round(daily * 11), abs=200)
    assert "assum" in f.text.lower() and "range" in f.text.lower()
    broke = cash_flow_forecast(ledger, TODAY, balance_minor=100000, days=30)
    assert broke.low_minor < 0 and "below zero" in broke.text.lower()


def test_safe_to_spend_is_what_is_not_spoken_for(tmp_path):
    ledger, acct = led(tmp_path)
    ledger.add_bill("Electricity", 90000, due_day=24)
    monthly(ledger, acct, "NETFLIX.COM", 64900, 25, 5, "entertainment", last_month=9)
    gid = ledger.add_goal("Laptop", 4000000, due="2027-03-20")
    ledger.contribute(gid, 1000000)  # Rs 6 000 a month needed
    s = safe_to_spend(ledger, TODAY, balance_minor=2000000)
    # balance 20 000 - electricity 900 - netflix 649 - laptop instalment 6 000 = 12 451, over 12 days (20-31 Oct)
    assert s.free_minor == 2000000 - 90000 - 64900 - 600000
    assert s.per_day_minor == s.free_minor // 12
    assert "spoken for" in s.text and "not financial advice" in s.text.lower()
    assert "balance" in safe_to_spend(ledger, TODAY, balance_minor=None).text.lower()


def test_the_daily_allowance_comes_from_the_discretionary_envelopes(tmp_path):
    ledger, acct = led(tmp_path)
    ledger.set_budget("food", 500000)
    ledger.set_budget("entertainment", 100000)
    ledger.set_budget("rent", 1500000)  # not discretionary
    add(ledger, acct, date(2026, 10, 18), -200000, "ZOMATO", "food")
    add(ledger, acct, date(2026, 10, 20), -30000, "CAFE", "food")
    a = daily_allowance(ledger, TODAY)
    # left: food 3 000 + entertainment 1 000 = 4 000, across 12 days (20-31 Oct) = Rs 333 a day; Rs 300 spent today
    assert a.per_day_minor == (500000 - 230000 + 100000) // 12
    assert a.spent_today_minor == 30000


def test_subscription_value_needs_your_usage_and_membership_value_uses_your_workout_count(tmp_path):
    ledger, acct = led(tmp_path)
    monthly(ledger, acct, "NETFLIX.COM", 64900, 5, 5, "entertainment")
    assert "tell me" in subscription_value(ledger, "netflix", uses=None, today=TODAY).lower()
    v = subscription_value(ledger, "netflix", uses=4, today=TODAY)
    assert "162.25" in v  # 649 over 4 uses in a month
    monthly(ledger, acct, "CULT FIT GYM", 150000, 12, 3, "health")
    hub = HealthHub(tmp_path / "h.db", clock=lambda: 1_800_000_000.0)
    hub.grant("training")
    for d in (13, 15, 17, 19):  # four sessions since the last charge on 12 Oct
        hub.add_workout("hevy", f"w{d}", datetime(2026, 10, d, 7).timestamp(), "Legs", 3600, [])
    m = membership_value(ledger, hub, "cult fit", TODAY)
    assert "375.00" in m and "4 sessions" in m  # Rs 1 500 over four sessions


def test_a_rule_by_voice_teaches_a_merchant_without_a_transaction(tmp_path):
    ledger, acct = led(tmp_path)
    ledger.add_batch(acct, [("2026-10-01", -30000, "UPI/RAVI KIRANA/1")], source="csv")
    from minty_core.finance_commands import FinanceCommands

    cmds = FinanceCommands(ledger, today=lambda: TODAY)
    reply = cmds.handle("anything from ravi kirana is groceries")
    assert "groceries" in reply and ledger.transactions()[0]["category"] == "groceries"
    ledger.add_batch(acct, [("2026-10-09", -21000, "UPI/RAVI KIRANA/2")], source="csv")
    assert ledger.transactions()[-1]["category"] == "groceries"


def test_notices_are_announced_once_per_bill_and_per_budget_threshold(tmp_path):
    from minty_core.finance_pack import pending_notices

    ledger, acct = led(tmp_path)
    ledger.add_bill("Electricity", 90000, due_day=22)
    ledger.set_budget("food", 500000)
    add(ledger, acct, date(2026, 10, 3), -410000, "ZOMATO", "food")
    texts, done = pending_notices(ledger, TODAY, {})
    assert any("Electricity" in t and "in 2 days" in t for t in texts) and any("82%" in t or "food" in t for t in texts)
    again, done2 = pending_notices(ledger, TODAY, done)
    assert again == [] and done2 == done  # nothing new, nothing repeated
    add(ledger, acct, date(2026, 10, 10), -100000, "SWIGGY", "food")  # crosses 100 %
    more, _ = pending_notices(ledger, TODAY, done)
    assert len(more) == 1 and "102%" in more[0]
    off, _ = pending_notices(ledger, TODAY, {}, insights=False)
    assert all("budget" not in t for t in off) and len(off) == 1
