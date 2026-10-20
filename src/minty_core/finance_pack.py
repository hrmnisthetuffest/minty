"""Money insight: budget alerts, subscription audit and value, bill calendar, 30-day forecast, safe to spend, daily
allowance, membership cost per session. Arithmetic over your ledger, with assumptions and ranges; never advice and no
way to move money."""
from __future__ import annotations

import calendar
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from minty_core.finance import Ledger, money
from minty_core.finance_insights import Recurring, budget_status, detect_recurring, next_after

DISCRETIONARY = {"food", "entertainment", "shopping", "transport", "snacks"}
QUIET_FACTOR = 1.5
RANGE = 0.2


def _recurring(ledger: Ledger, today: date) -> tuple[list[Recurring], list[Recurring], list[Recurring]]:
    """(active expenses, quiet expenses, active income)."""
    rows = ledger.transactions()
    active, quiet = [], []
    for r in detect_recurring(rows):
        gone = (today - datetime.strptime(r.last_day, "%Y-%m-%d").date()).days > QUIET_FACTOR * r.interval_days
        (quiet if gone else active).append(r)
    inc = [r for r in detect_recurring(rows, income=True)
           if (today - datetime.strptime(r.last_day, "%Y-%m-%d").date()).days <= QUIET_FACTOR * r.interval_days]
    return active, quiet, inc


def _per_year(interval: int) -> int:
    for lo, hi, n in ((6, 8, 52), (13, 16, 26), (27, 33, 12), (85, 96, 4), (350, 380, 1)):
        if lo <= interval <= hi:
            return n
    return max(1, round(365 / max(interval, 1)))


# ---- 800 budget alerts ---------------------------------------------------------------------------------------
@dataclass
class Alert:
    category: str
    threshold: int  # 80, 100, or 0 for heading over
    text: str


def budget_alerts(ledger: Ledger, month: str, today: date) -> list[Alert]:
    out = []
    for b in budget_status(ledger, month, today):
        if b.pct >= 100:
            out.append(Alert(b.category, 100, f"{b.category}: you've used {b.pct}% of the {money(b.limit_minor)} "
                                               f"budget ({money(b.spent_minor)} spent)."))
        elif b.pct >= 80:
            out.append(Alert(b.category, 80, f"{b.category}: {b.pct}% of the {money(b.limit_minor)} budget used, "
                                              f"{money(b.remaining_minor)} left."))
        elif b.status == "heading over":
            out.append(Alert(b.category, 0, f"{b.category} is heading over its {money(b.limit_minor)} budget: "
                                            f"about {money(b.projected_minor)} at this pace."))
    return out


# ---- 801 subscription audit, 913 value ---------------------------------------------------------------------
@dataclass
class QuietSub:
    merchant: str
    days_since: int


@dataclass
class Audit:
    yearly_minor: int
    active: list[Recurring]
    overlaps: list[str]
    quiet: list[QuietSub]
    text: str


def _category_of(ledger_rows: list[dict], merchant: str) -> str:
    cats = [t["category"] for t in ledger_rows if t["merchant"] == merchant and t["category"]]
    return max(set(cats), key=cats.count) if cats else ""


def subscription_audit(ledger: Ledger, today: date) -> Audit:
    rows = ledger.transactions()
    active, quiet, _ = _recurring(ledger, today)
    yearly = sum(r.typical_minor * _per_year(r.interval_days) for r in active)
    by_cat: dict[str, list[str]] = {}
    for r in active:
        cat = _category_of(rows, r.merchant)
        if cat:
            by_cat.setdefault(cat, []).append(r.merchant)
    words = {2: "two", 3: "three", 4: "four"}
    overlaps = [f"You have {words.get(len(ms), str(len(ms)))} {cat} subscriptions ({', '.join(ms)}): worth checking "
                "you use each." for cat, ms in by_cat.items() if len(ms) >= 2]
    quiet_rows = [QuietSub(r.merchant, (today - datetime.strptime(r.last_day, "%Y-%m-%d").date()).days)
                  for r in quiet]
    listing = "; ".join(f"{r.merchant} {money(r.typical_minor)} every ~{r.interval_days} days" for r in active)
    text = (f"{len(active)} recurring charge{'s' * (len(active) != 1)}, about {money(yearly)} a year"
            + (f": {listing}." if listing else ".") + " "
            + " ".join(overlaps)
            + (" Not charged lately (may have ended): " + ", ".join(f"{q.merchant} ({q.days_since} days)"
                                                                      for q in quiet_rows) + "." if quiet_rows else ""))
    return Audit(yearly, active, overlaps, quiet_rows, text.strip())


def subscription_value(ledger: Ledger, name: str, uses: int | None, today: date) -> str:
    active, _, _ = _recurring(ledger, today)
    sub = next((r for r in active if name.lower() in r.merchant.lower()), None)
    if sub is None:
        return f"I don't see a recurring charge matching {name}."
    if not uses:
        return (f"{sub.merchant} costs {money(sub.typical_minor)} every {sub.interval_days} days. Tell me how many "
                "times you used it in that time and I'll work out the cost per use.")
    return (f"{sub.merchant}: {money(sub.typical_minor)} over {uses} use{'s' * (uses != 1)} is "
            f"{money(round(sub.typical_minor / uses))} each.")


# ---- 802 bill calendar ----------------------------------------------------------------------------------------
@dataclass
class CalItem:
    day: str
    name: str
    minor: int
    source: str  # bill | recurring | income


def _bill_dates(due_day: int, start: date, end: date) -> list[date]:
    out, y, m = [], start.year, start.month
    while date(y, m, 1) <= end:
        d = date(y, m, min(due_day, calendar.monthrange(y, m)[1]))
        if start <= d <= end:
            out.append(d)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _events(ledger: Ledger, start: date, end: date) -> list[CalItem]:
    active, _, income = _recurring(ledger, start)
    bills = ledger.bills()
    bill_words = {w for b in bills for w in b["name"].lower().split() if len(w) >= 4}
    items = []
    for b in bills:
        for d in _bill_dates(b["due_day"], start, end):
            items.append(CalItem(d.isoformat(), b["name"], b["amount_minor"], "bill"))
    for src, rows in (("recurring", active), ("income", income)):
        for r in rows:
            if src == "recurring" and any(w in r.merchant.lower() for w in bill_words):
                continue  # already a bill you set
            d = datetime.strptime(r.next_day, "%Y-%m-%d").date()
            while d < start:
                d = next_after(d, r.interval_days)
            while d <= end:
                items.append(CalItem(d.isoformat(), r.merchant, r.typical_minor, src))
                d = next_after(d, r.interval_days)
    return sorted(items, key=lambda i: (i.day, i.name))


def bill_calendar(ledger: Ledger, today: date, days: int = 30) -> list[CalItem]:
    return [i for i in _events(ledger, today, today + timedelta(days=days)) if i.source != "income"]


# ---- 910 forecast ---------------------------------------------------------------------------------------------
@dataclass
class Forecast:
    end_balance_minor: int
    low_minor: int
    low_day: str
    text: str


def _daily_spend(ledger: Ledger, today: date) -> float:
    active, quiet, _ = _recurring(ledger, today)
    skip = {r.merchant for r in active + quiet}
    bill_words = {w for b in ledger.bills() for w in b["name"].lower().split() if len(w) >= 4}
    since = (today - timedelta(days=30)).isoformat()
    total = 0
    for t in ledger.transactions(since, today.isoformat()):
        if t["amount_minor"] >= 0 or t["merchant"] in skip:
            continue
        if any(w in t["description"].lower() for w in bill_words):
            continue
        total += -t["amount_minor"]
    return total / 30


def cash_flow_forecast(ledger: Ledger, today: date, balance_minor: int, days: int = 30) -> Forecast:
    end_day = today + timedelta(days=days)
    events = _events(ledger, today + timedelta(days=1), end_day)
    daily = _daily_spend(ledger, today)
    balance, low, low_day = float(balance_minor), float(balance_minor), today.isoformat()
    for i in range(1, days + 1):
        d = today + timedelta(days=i)
        for e in events:
            if e.day == d.isoformat():
                balance += e.minor if e.source == "income" else -e.minor
        balance -= daily
        if balance < low:
            low, low_day = balance, d.isoformat()
    spread = daily * days * RANGE
    text = (f"Over the next {days} days you start at {money(balance_minor)} and end near {money(round(balance))} "
            f"(range {money(round(balance - spread))} to {money(round(balance + spread))}); the lowest point is about "
            f"{money(round(low))} on {low_day}. Assumptions: the bills and repeating charges and income I've found "
            f"arrive on time, and everyday spending stays at about {money(round(daily))} a day.")
    if low < 0:
        text += " That goes below zero: something needs to move, or a bill or a purchase wait."
    return Forecast(round(balance), round(low), low_day, text)


# ---- 911 safe to spend, 1036 allowance --------------------------------------------------------------------
@dataclass
class Safe:
    free_minor: int | None
    per_day_minor: int | None
    text: str


def safe_to_spend(ledger: Ledger, today: date, balance_minor: int | None) -> Safe:
    if balance_minor is None:
        return Safe(None, None, "I need your current balance to say what's safe to spend; tell me what the account "
                                "shows now.")
    last = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
    committed = sum(e.minor for e in _events(ledger, today, last) if e.source != "income")
    goals = sum(g["monthly_needed_minor"] or 0 for g in ledger.goals(today))
    days_left = (last - today).days + 1
    free = balance_minor - committed - goals
    per_day = free // days_left
    text = (f"Of {money(balance_minor)}, bills and repeating charges still due this month take {money(committed)} and "
            f"your savings goals {money(goals)}. That leaves {money(free)} not spoken for, about {money(per_day)} a day "
            f"for {days_left} days. This is from your own numbers, not financial advice.")
    return Safe(free, per_day, text)


@dataclass
class Allowance:
    per_day_minor: int
    spent_today_minor: int
    text: str


def daily_allowance(ledger: Ledger, today: date) -> Allowance:
    month = today.strftime("%Y-%m")
    lines = [b for b in budget_status(ledger, month, today) if b.category in DISCRETIONARY]
    if not lines:
        return Allowance(0, 0, "Set budgets for food, entertainment, shopping or transport first, and I'll turn what "
                               "is left into a daily allowance.")
    left = sum(max(0, b.remaining_minor) for b in lines)
    days_left = calendar.monthrange(today.year, today.month)[1] - today.day + 1
    spent_today = sum(-t["amount_minor"] for t in ledger.transactions(today.isoformat(), today.isoformat())
                      if t["amount_minor"] < 0 and t["category"] in DISCRETIONARY)
    per_day = left // days_left
    return Allowance(per_day, spent_today,
                     f"{money(left)} is left across your everyday envelopes for {days_left} days: about "
                     f"{money(per_day)} a day. You've spent {money(spent_today)} today.")


# ---- 1039 membership value -----------------------------------------------------------------------------------
def membership_value(ledger: Ledger, hub, name: str, today: date) -> str:
    charges = [t for t in ledger.transactions() if name.lower() in t["description"].lower() and t["amount_minor"] < 0]
    if not charges:
        return f"I don't see any payments matching {name}."
    last = charges[-1]
    since = datetime.strptime(last["day"], "%Y-%m-%d")
    sessions = [w for w in hub.rows("workouts") if w["started"] >= since.timestamp()]
    n = len(sessions)
    if n == 0:
        return (f"{name}: {money(-last['amount_minor'])} paid on {last['day']} and no sessions logged since, so "
                "no cost per session yet.")
    return (f"{name}: {money(-last['amount_minor'])} paid on {last['day']} and {n} session{'s' * (n != 1)} since, "
            f"{money(round(-last['amount_minor'] / n))} each.")


# ---- the notices the app announces: bills due soon and budget thresholds, each once -----------------------
def pending_notices(ledger: Ledger, today: date, done: dict, insights: bool = True) -> tuple[list[str], dict]:
    """(texts to announce now, the updated `done` map). A bill is announced once per due date; a budget threshold
    once per month and category."""
    from minty_core.finance_insights import due_bills

    out, done = [], dict(done)
    for bill in due_bills(ledger, today, 3):
        key = f"bill|{bill['name']}|{bill['due']}"
        if key not in done:
            done[key] = today.isoformat()
            when = "today" if bill["days"] == 0 else f"in {bill['days']} day{'s' * (bill['days'] != 1)}"
            out.append(f"Reminder: {bill['name']}, {money(bill['amount_minor'])}, is due {when}.")
    if insights:
        month = today.strftime("%Y-%m")
        for a in budget_alerts(ledger, month, today):
            key = f"budget|{month}|{a.category}|{a.threshold}"
            if key not in done:
                done[key] = today.isoformat()
                out.append(a.text)
    keep_from = today.replace(day=1).isoformat()
    return out, {k: v for k, v in done.items() if v >= keep_from}
