"""Budgets, goals, subscriptions, bills, the monthly report and insights, all from the ledger and all informational.

`facts_pack` is the only text built for a model: totals and categories, never a merchant, description or account.
"""
from __future__ import annotations

import calendar
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from minty_core.finance import Ledger, merchant_key, money

BAND = ((6, 8), (13, 16), (27, 33), (85, 96), (350, 380))
UNUSUAL_FACTOR = 3.0
UNUSUAL_MIN_SAMPLES = 5
CHANGE_FLAG_PCT = 25


def _month_bounds(month: str) -> tuple[str, str, int]:
    y, m = (int(x) for x in month.split("-"))
    last = calendar.monthrange(y, m)[1]
    return f"{month}-01", f"{month}-{last:02d}", last


def _prev_month(month: str) -> str:
    y, m = (int(x) for x in month.split("-"))
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def _spend_by_category(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in rows:
        if t["amount_minor"] < 0:
            out[t["category"] or "uncategorised"] = out.get(t["category"] or "uncategorised", 0) - t["amount_minor"]
    return out


# ---- budgets -------------------------------------------------------------------------------------------------
@dataclass
class BudgetLine:
    category: str
    limit_minor: int
    spent_minor: int
    remaining_minor: int
    pct: int
    projected_minor: int
    status: str  # on track | heading over | over


def budget_status(ledger: Ledger, month: str, today: date) -> list[BudgetLine]:
    first, last, days = _month_bounds(month)
    spent = _spend_by_category(ledger.transactions(first, last))
    elapsed = today.day if today.strftime("%Y-%m") == month else days
    out = []
    for category, limit in sorted(ledger.budgets().items()):
        s = spent.get(category, 0)
        projected = round(s / max(1, elapsed) * days)
        status = "over" if s > limit else "heading over" if projected > limit else "on track"
        out.append(BudgetLine(category, limit, s, limit - s, round(100 * s / limit), projected, status))
    return out


# ---- subscriptions -------------------------------------------------------------------------------------------
@dataclass
class Recurring:
    merchant: str
    typical_minor: int
    interval_days: int
    count: int
    last_day: str
    next_day: str
    kind: str  # subscription | bill


def add_month(d: date) -> date:
    """The same day of the next month (clamped), so a charge on the 28th stays on the 28th."""
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def next_after(d: date, interval_days: int) -> date:
    return add_month(d) if 27 <= interval_days <= 33 else d + timedelta(days=interval_days)


def detect_recurring(transactions: list[dict], income: bool = False) -> list[Recurring]:
    groups: dict[str, list[dict]] = {}
    for t in transactions:
        key = t.get("merchant") or merchant_key(t["description"])
        if key and (t["amount_minor"] > 0 if income else t["amount_minor"] < 0):
            groups.setdefault(key, []).append(t)
    found = []
    for key, rows in groups.items():
        if len(rows) < 3:
            continue
        rows.sort(key=lambda t: t["day"])
        amounts = [abs(t["amount_minor"]) for t in rows]
        med_amount = statistics.median(amounts)
        days = [datetime.strptime(t["day"], "%Y-%m-%d").date() for t in rows]
        gaps = [(b - a).days for a, b in zip(days, days[1:])]
        med_gap = statistics.median(gaps)
        band = next((b for b in BAND if b[0] <= med_gap <= b[1]), None)
        if band is None:
            continue
        # calendar months are 28-31 days: "regular" means every gap sits in the same band as the typical one
        regular = sum(1 for g in gaps if band[0] - 1 <= g <= band[1] + 1) / len(gaps)
        fixed = sum(1 for a in amounts if abs(a - med_amount) <= 0.1 * med_amount) / len(amounts)
        if regular >= 0.75 and fixed >= 0.8:
            kind = "subscription"
        elif len(rows) >= 4 and regular >= 0.9 and all(abs(a - med_amount) <= 0.4 * med_amount for a in amounts):
            kind = "bill"
        else:
            continue
        last = days[-1]
        found.append(Recurring(key, int(med_amount), int(round(med_gap)), len(rows), last.isoformat(),
                               next_after(last, int(round(med_gap))).isoformat(), kind))
    return sorted(found, key=lambda r: r.merchant)


# ---- bills ---------------------------------------------------------------------------------------------------
def _due_date(due_day: int, today: date) -> date:
    def clamp(y, m):
        return date(y, m, min(due_day, calendar.monthrange(y, m)[1]))

    this = clamp(today.year, today.month)
    if this >= today:
        return this
    y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return clamp(y, m)


def due_bills(ledger: Ledger, today: date, within_days: int = 3) -> list[dict]:
    month_start = today.replace(day=1).isoformat()
    spent_text = " ".join(t["description"].lower() for t in ledger.transactions(month_start, today.isoformat())
                          if t["amount_minor"] < 0)
    out = []
    for bill in ledger.bills():
        due = _due_date(bill["due_day"], today)
        if (due - today).days > within_days:
            continue
        words = [w for w in bill["name"].lower().split() if len(w) >= 4]
        if due.month == today.month and any(w in spent_text for w in words):
            continue  # already paid this month
        out.append({**bill, "due": due.isoformat(), "days": (due - today).days})
    return sorted(out, key=lambda b: b["due"])


# ---- the report ----------------------------------------------------------------------------------------------
@dataclass
class Report:
    month: str
    income_minor: int
    spent_minor: int
    net_minor: int
    text: str


def monthly_report(ledger: Ledger, month: str) -> Report:
    first, last, _ = _month_bounds(month)
    rows = ledger.transactions(first, last)
    income = sum(t["amount_minor"] for t in rows if t["amount_minor"] > 0)
    by_cat = _spend_by_category(rows)
    spent = sum(by_cat.values())
    prev_first, prev_last, _ = _month_bounds(_prev_month(month))
    prev = _spend_by_category(ledger.transactions(prev_first, prev_last))
    parts = [f"{month}: income {money(income)}, spent {money(spent)}, net {money(income - spent)}."]
    cats = []
    for category, amount in sorted(by_cat.items(), key=lambda kv: -kv[1])[:6]:
        note = ""
        before = prev.get(category)
        if before:
            change = round(100 * (amount - before) / before)
            if abs(change) >= CHANGE_FLAG_PCT:
                note = f" ({'up' if change > 0 else 'down'} {abs(change)}% on last month)"
        cats.append(f"{category} {money(amount)}{note}")
    if cats:
        parts.append("By category: " + ", ".join(cats) + ".")
    merchants: dict[str, int] = {}
    for t in rows:
        if t["amount_minor"] < 0 and t["merchant"]:
            merchants[t["merchant"]] = merchants.get(t["merchant"], 0) - t["amount_minor"]
    if merchants:
        top = sorted(merchants.items(), key=lambda kv: -kv[1])[:3]
        parts.append("Most paid to: " + ", ".join(f"{m} {money(a)}" for m, a in top) + ".")
    return Report(month, income, spent, income - spent, " ".join(parts))


def facts_pack(ledger: Ledger, month: str, today: date) -> str:
    """For a model: totals and categories, budget status. No merchant, description, account or date of a row."""
    first, last, _ = _month_bounds(month)
    rows = ledger.transactions(first, last)
    income = sum(t["amount_minor"] for t in rows if t["amount_minor"] > 0)
    by_cat = _spend_by_category(rows)
    budgets = {b.category: b for b in budget_status(ledger, month, today)}
    lines = [f"{month}: income {money(income)}, spent {money(sum(by_cat.values()))}."]
    cats = []
    for category, amount in sorted(by_cat.items(), key=lambda kv: -kv[1])[:8]:
        b = budgets.get(category)
        cats.append(f"{category} {money(amount)}" + (f" (budget {money(b.limit_minor)}, {b.status})" if b else ""))
    if cats:
        lines.append("By category: " + ", ".join(cats) + ".")
    return " ".join(lines)


# ---- insights --------------------------------------------------------------------------------------------------
def unusual(transactions: list[dict], today: date) -> list[dict]:
    """Purchases in the last 30 days that are over three times the usual in their category."""
    since = (today - timedelta(days=30)).isoformat()
    flagged = []
    for t in transactions:
        if t["amount_minor"] >= 0 or t["day"] < since or not t["category"]:
            continue
        before = [-x["amount_minor"] for x in transactions
                  if x["category"] == t["category"] and x["amount_minor"] < 0 and x["day"] < t["day"]]
        if len(before) >= UNUSUAL_MIN_SAMPLES and -t["amount_minor"] > UNUSUAL_FACTOR * statistics.median(before):
            flagged.append(t)
    return flagged


@dataclass
class Afford:
    ok: bool
    remaining_minor: int
    text: str


def afford(ledger: Ledger, amount_minor: int, category: str, today: date) -> Afford:
    """From your own envelope only. Not advice."""
    month = today.strftime("%Y-%m")
    line = next((b for b in budget_status(ledger, month, today) if b.category == category.lower()), None)
    if line is None:
        return Afford(True, 0, f"You haven't set a {category} budget, so I can't compare. (Informational, not "
                               "financial advice.)")
    ok = amount_minor <= line.remaining_minor
    return Afford(ok, line.remaining_minor,
                  f"Your {category} envelope for {month} has {money(line.remaining_minor)} left; this would be "
                  f"{money(amount_minor)}, so it {'fits' if ok else 'does not fit'}. This is from your own numbers, "
                  "not financial advice.")
