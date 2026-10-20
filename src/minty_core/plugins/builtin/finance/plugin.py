"""Finance tools. Read-only: nothing here can move money. A model gets `summary` (totals and categories); rows are
shown to you by `show` (Tool.local_only). Off until `finance.enabled`."""
import os
import time
from datetime import date

from minty_core.finance import money, parse_money
from minty_core.finance_import import parse_quick
from minty_core.finance_insights import afford, budget_status, detect_recurring, due_bills, facts_pack, monthly_report
from minty_core import finance_pack
from minty_core.scamcheck import check as scam_check
from minty_core.plugins.host import Tool

MAX_ROWS = 15


def register(ctx):
    def cfg():
        return ctx.services["config"]().finance

    def ledger():
        if not cfg().enabled:
            raise ValueError("The money tracker is switched off (finance.enabled on the Switches page).")
        led = ctx.services.get("finance")
        if led is None:
            raise ValueError("The finance store isn't available.")
        return led

    def today() -> date:
        return date.fromtimestamp(ledger()._clock())

    def month_of(args) -> str:
        m = str(args.get("month") or "")
        return m if len(m) == 7 and m[4] == "-" else today().strftime("%Y-%m")

    def default_account(led) -> int:
        return led.add_account(cfg().default_account or "Cash", kind="cash")

    def add(args):
        led = ledger()
        q = parse_quick(str(args.get("text") or ""), today())
        if q is None:
            raise ValueError("Say it like: spent 200 on lunch, or: received 500 from dad")
        tid = led.add_manual(default_account(led), q.day, q.minor, q.description)
        row = next(t for t in led.transactions(q.day, q.day) if t["id"] == tid)
        return (f"Added {'spend' if q.minor < 0 else 'income'} of {money(abs(q.minor))}: {q.description} "
                f"({row['category'] or 'uncategorised'}) on {q.day}.")

    def correct(args):
        led = ledger()
        match = str(args.get("match") or "").strip().lower()
        category = str(args.get("category") or "").strip()
        if not match or not category:
            raise ValueError("say which payment and which category")
        rows = [t for t in led.transactions() if match in t["description"].lower()]
        if not rows:
            return f"I have no transaction mentioning '{match}'."
        n = led.set_category(rows[-1]["id"], category)
        return (f"Done: {n} transaction{'s' * (n != 1)} now {category.lower()}, and I'll file "
                f"{rows[-1]['merchant'] or 'that'} there from now on.")

    def set_(args):
        led = ledger()
        kind, name = str(args.get("kind")), str(args.get("name") or "").strip()
        amount = parse_money(str(args.get("amount") or "0"))
        if kind == "budget":
            led.set_budget(name, amount)
            return f"{name.lower()} budget set to {money(amount)} a month."
        if kind == "goal":
            led.add_goal(name, amount, str(args.get("due") or ""))
            return f"Savings goal '{name}': {money(amount)}" + (f" by {args['due']}." if args.get("due") else ".")
        if kind == "bill":
            led.add_bill(name, amount, int(args.get("due_day") or 0))
            return f"{name} ({money(amount)}) due on day {int(args['due_day'])} each month."
        if kind == "contribute":
            goal = next((g for g in led.goals(today()) if g["name"].lower() == name.lower()), None)
            if goal is None:
                raise ValueError(f"no goal called {name}")
            led.contribute(goal["id"], amount)
            pct = next(g["pct"] for g in led.goals(today()) if g["id"] == goal["id"])
            return f"Added {money(amount)} to {goal['name']} ({pct}% there)."
        raise ValueError("kind is budget, goal, bill or contribute")

    def summary(args):
        led = ledger()
        month = month_of(args)
        parts = [facts_pack(led, month, today())]
        subs = detect_recurring(led.transactions())
        if subs:
            parts.append(f"{len(subs)} recurring charge{'s' * (len(subs) != 1)} found, about "
                         f"{money(sum(r.typical_minor for r in subs))} a month in total.")
        due = due_bills(led, today(), 7)
        if due:
            parts.append(f"{len(due)} bill{'s' * (len(due) != 1)} due in the next week.")
        return " ".join(parts)

    def show(args):
        led = ledger()
        what = str(args.get("what") or "transactions")
        month = month_of(args)
        if what == "report":
            return monthly_report(led, month).text
        if what in ("audit", "calendar", "value", "membership"):
            if not cfg().insights:
                raise ValueError("Money insights are switched off (finance.insights).")
            d = today()
            if what == "audit":
                return finance_pack.subscription_audit(led, d).text
            if what == "calendar":
                items = finance_pack.bill_calendar(led, d, int(args.get("days") or 30))
                return "\n".join(f"{i.day} {i.name} {money(i.minor)} ({i.source})" for i in items) or \
                    "Nothing due in that time."
            if what == "value":
                uses = int(args["uses"]) if str(args.get("uses") or "").isdigit() else None
                return finance_pack.subscription_value(led, str(args.get("name") or ""), uses, d)
            hub = ctx.services.get("health")
            if hub is None:
                return "The health store is off, so I can't count sessions."
            return finance_pack.membership_value(led, hub, str(args.get("name") or ""), d)
        if what == "subscriptions":
            rows = [f"{r.merchant}: {money(r.typical_minor)} every ~{r.interval_days} days, next {r.next_day} "
                    f"({r.kind})" for r in detect_recurring(led.transactions())]
        elif what == "bills":
            rows = [f"{b['name']}: {money(b['amount_minor'])} due {b['due']}" for b in due_bills(led, today(), 40)]
        elif what == "goals":
            rows = [f"{g['name']}: {money(g['saved_minor'])} of {money(g['target_minor'])} ({g['pct']}%)"
                    + (f", {money(g['monthly_needed_minor'])} a month to make {g['due']}"
                       if g["monthly_needed_minor"] is not None else "") for g in led.goals(today())]
        elif what == "budgets":
            rows = [f"{b.category}: {money(b.spent_minor)} of {money(b.limit_minor)} ({b.status})"
                    for b in budget_status(led, month, today())]
        else:
            first, last = f"{month}-01", f"{month}-31"
            rows = [f"{t['day']} {money(t['amount_minor'])} {t['description']} [{t['category'] or '?'}]"
                    for t in reversed(led.transactions(first, last))]
        if not rows:
            return f"Nothing to show for {what}."
        extra = f"\n… and {len(rows) - MAX_ROWS} more." if len(rows) > MAX_ROWS else ""
        return "\n".join(rows[:MAX_ROWS]) + extra

    def afford_(args):
        led = ledger()
        return afford(led, parse_money(str(args.get("amount") or "0")), str(args.get("category") or ""), today()).text

    def export(args):
        led = ledger()
        fmt = str(args.get("format") or "csv")
        body = led.export_beancount() if fmt == "beancount" else led.export_csv()
        folder = ctx.config_dir / "exports"
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o700)
        path = folder / f"finance-{time.strftime('%Y%m%d-%H%M%S')}.{'beancount' if fmt == 'beancount' else 'csv'}"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        return f"Exported {led.count()} transactions to {path} (only you can read it)."

    def insight(args):
        led = ledger()
        if not cfg().insights:
            raise ValueError("Money insights are switched off (finance.insights).")
        what, d = str(args.get("what") or "alerts"), today()
        month = month_of(args)
        balance = None
        if str(args.get("balance") or "").strip():
            balance = parse_money(str(args["balance"]))
        if what == "alerts":
            return " ".join(a.text for a in finance_pack.budget_alerts(led, month, d)) or "No budget needs attention."
        if what == "forecast":
            if balance is None:
                return "Tell me what the account shows now (balance) and I'll forecast the next 30 days."
            return finance_pack.cash_flow_forecast(led, d, balance, int(args.get("days") or 30)).text
        if what == "safe":
            return finance_pack.safe_to_spend(led, d, balance).text
        if what == "allowance":
            return finance_pack.daily_allowance(led, d).text
        raise ValueError("what is alerts, forecast, safe or allowance (names and lists are in finance_show)")

    def scam(args):
        c = scam_check(str(args.get("text") or ""))
        return c.text

    def delete(args):
        n = ledger().delete_all()
        return f"Deleted {n} transaction{'s' * (n != 1)}."

    def delete_describe(args):
        try:
            n = ledger().count()
        except ValueError:
            n = 0
        return f"Delete all {n} transactions from your money tracker, for good"

    def schema(**props):
        return {"type": "object", "properties": {k: v for k, (v, _) in props.items()},
                "required": [k for k, (_, req) in props.items() if req]}

    s = {"type": "string"}
    return [
        Tool("summary", "Money this month: totals, categories, budgets (no merchants).",
             schema(month=(s, False)), summary, sensitive=True),
        Tool("show", "Show the user their rows and named lists, for them only (what: transactions, report, "
                     "subscriptions, bills, goals, budgets, audit, calendar, value, membership; name, uses).",
             schema(what=(s, False), month=(s, False), name=(s, False), uses=(s, False),
                    days=({"type": "integer"}, False)), show, sensitive=True, local_only=True),
        Tool("add", "Record a spend or income from a phrase like 'spent 200 on lunch'.",
             schema(text=(s, True)), add, risk=1, sensitive=True),
        Tool("correct", "Fix a category: every payment like it, now and later.",
             schema(match=(s, True), category=(s, True)), correct, risk=1, sensitive=True),
        Tool("set", "Set a budget, savings goal, bill or add to a goal (kind, name, amount, due, due_day).",
             schema(kind=({"type": "string", "enum": ["budget", "goal", "bill", "contribute"]}, True),
                    name=(s, True), amount=(s, True), due=(s, False), due_day=({"type": "integer"}, False)),
             set_, risk=1, sensitive=True),
        Tool("afford", "Whether an amount fits the category's budget left (informational).",
             schema(amount=(s, True), category=(s, True)), afford_, sensitive=True),
        Tool("export", "Write the ledger to a local CSV or beancount file (format).", schema(format=(s, False)),
             export, risk=1, sensitive=True),
        Tool("insight", "Money insight without names: what = alerts, forecast, safe, allowance (balance, days).",
             schema(what=({"type": "string", "enum": ["alerts", "forecast", "safe", "allowance"]}, True),
                    balance=(s, False), days=({"type": "integer"}, False), month=(s, False)), insight,
             sensitive=True),
        Tool("scam", "Check pasted message text for scam signs. Never opens links.", schema(text=(s, True)), scam),
        Tool("delete_data", "Delete every transaction. Always asks.", schema(), delete, risk=3,
             describe=delete_describe),
    ]
