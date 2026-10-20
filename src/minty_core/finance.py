"""The finance ledger: a private, read-only record of money in and out.

Amounts are integer paise (no floating point). Nothing here talks to a bank, a network or a payment system: it
reads what you give it (quick add, a CSV, forwarded SMS text) and reports. Raw rows are residency FINANCIAL_LOCAL: a
model only ever gets totals and categories (see the finance plugin).
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

CURRENCY = "INR"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, kind TEXT NOT NULL DEFAULT 'bank',
    currency TEXT NOT NULL DEFAULT 'INR');
CREATE TABLE IF NOT EXISTS transactions(id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id)
    ON DELETE CASCADE, day TEXT NOT NULL, amount_minor INTEGER NOT NULL, description TEXT NOT NULL,
    merchant TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '', category_source TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual', ext_hash TEXT NOT NULL, created REAL NOT NULL,
    UNIQUE(account_id, ext_hash));
CREATE INDEX IF NOT EXISTS tx_day ON transactions(day);
CREATE TABLE IF NOT EXISTS rules(merchant TEXT PRIMARY KEY, category TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS budgets(category TEXT PRIMARY KEY, limit_minor INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS goals(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, target_minor INTEGER NOT NULL,
    saved_minor INTEGER NOT NULL DEFAULT 0, due TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS bills(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, amount_minor INTEGER NOT NULL,
    due_day INTEGER NOT NULL, category TEXT NOT NULL DEFAULT 'bills');
"""

_STOP = {"UPI", "DR", "CR", "NEFT", "IMPS", "RTGS", "POS", "ECOM", "PURCHASE", "PAYMENT", "PAY", "TXN", "REF", "REFNO",
         "OKAXIS", "OKHDFCBANK", "OKICICI", "OKSBI", "YBL", "PAYTM", "IBL", "AXL", "APL", "BANK", "TO", "FROM", "AC",
         "VPA", "INFO", "ATM", "WDL", "CASH", "SI", "ACH", "MANDATE", "DEBIT", "CREDIT", "CARD", "THE", "AND"}

# category -> keywords (lower case, matched anywhere in the description); the first category that matches wins
BUILTIN = [
    ("income", ("salary", "pocket money", "stipend", "interest credit", "cashback", "refund", "scholarship")),
    ("food", ("zomato", "swiggy", "restaurant", "cafe", "coffee", "lunch", "dinner", "breakfast", "pizza", "burger",
              "dominos", "kfc", "mcdonald", "starbucks", "bakery", "canteen", "chai")),
    ("groceries", ("grocery", "supermarket", "bigbasket", "blinkit", "zepto", "dmart", "instamart", "kirana",
                   "vegetable", "milk")),
    ("transport", ("uber", "ola cabs", "rapido", "metro", "petrol", "fuel", "irctc", "railway", "bus ticket",
                   "auto fare", "parking")),
    ("education", ("school", "tuition", "fees", "stationery", "bookstore", "book depot", "udemy", "coursera",
                   "exam", "coaching")),
    ("entertainment", ("netflix", "spotify", "prime video", "hotstar", "youtube premium", "movie", "bookmyshow",
                       "steam", "playstation", "gaming")),
    ("shopping", ("amazon", "flipkart", "myntra", "ajio", "meesho", "decathlon")),
    ("health", ("pharmacy", "medplus", "apollo", "chemist", "gym", "cult.fit", "cultfit", "hospital", "clinic")),
    ("bills", ("electricity", "recharge", "jio", "airtel", "vodafone", "broadband", "bsnl", "water bill",
               "gas bill", "dth")),
]


def parse_money(text: str) -> int:
    """'1,234.50' / 'Rs. 200' / '(450.00)' / '-12.05' -> signed paise. Raises ValueError on anything else."""
    raw = str(text or "").strip()
    negative = raw.startswith("(") and raw.endswith(")") or raw.startswith("-") or raw.upper().endswith("DR")
    cleaned = re.sub(r"(?i)rs\.?|inr|₹|cr$|dr$|[,\s()\-+]", "", raw)
    if not re.fullmatch(r"\d+(\.\d{1,2})?|\.\d{1,2}", cleaned):
        raise ValueError(f"not an amount: {text!r}")
    whole, _, frac = cleaned.partition(".")
    minor = int(whole or "0") * 100 + int((frac + "00")[:2])
    return -minor if negative else minor


def money(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    return f"{sign}{abs(minor) // 100}.{abs(minor) % 100:02d}"


def merchant_key(description: str) -> str:
    tokens = [t for t in re.split(r"[^A-Z0-9]+", (description or "").upper()) if t]
    keep = [t for t in tokens if len(t) > 1 and t not in _STOP and not t.startswith("REF")
            and sum(c.isdigit() for c in t) < 3]
    return " ".join(keep[:2])


def categorize_builtin(description: str) -> str:
    low = (description or "").lower()
    for category, words in BUILTIN:
        if any(w in low for w in words):
            return category
    return ""


@dataclass
class BatchResult:
    added: int = 0
    duplicates: int = 0
    ids: list[int] = field(default_factory=list)


class Ledger:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path, self._clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.close(os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600))
        os.chmod(self.path, 0o600)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- accounts ---------------------------------------------------------------------------------
    def add_account(self, name: str, kind: str = "bank", currency: str = CURRENCY) -> int:
        name = " ".join(name.split())[:60]
        if not name:
            raise ValueError("an account needs a name")
        with self._lock, self._db:
            self._db.execute("INSERT OR IGNORE INTO accounts(name,kind,currency) VALUES(?,?,?)",
                             (name, kind, currency))
            return self._db.execute("SELECT id FROM accounts WHERE name=?", (name,)).fetchone()[0]

    def accounts(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM accounts ORDER BY id")]

    # ---- categorising -----------------------------------------------------------------------------
    def rules(self) -> dict[str, str]:
        with self._lock:
            return {r["merchant"]: r["category"] for r in self._db.execute("SELECT merchant, category FROM rules")}

    @staticmethod
    def _rule_matches(rule: str, key: str) -> bool:
        return rule == key or rule in key.split()

    def categorize(self, description: str) -> tuple[str, str]:
        """(category, where it came from: rule | builtin | '')."""
        key = merchant_key(description)
        if key:
            for rule, category in self.rules().items():
                if self._rule_matches(rule, key):
                    return category, "rule"
        built = categorize_builtin(description)
        return (built, "builtin") if built else ("", "")

    def set_category(self, txn_id: int, category: str, learn: bool = True) -> int:
        """Your correction. With `learn`, the whole merchant is taught and older unfixed rows are corrected too.
        Returns how many rows changed."""
        category = " ".join(category.lower().split())[:30]
        with self._lock, self._db:
            row = self._db.execute("SELECT merchant FROM transactions WHERE id=?", (txn_id,)).fetchone()
            if row is None:
                raise ValueError("no such transaction")
            self._db.execute("UPDATE transactions SET category=?, category_source='manual' WHERE id=?",
                             (category, txn_id))
            changed = 1
            if learn and row["merchant"]:
                self._db.execute("INSERT INTO rules(merchant,category,created) VALUES(?,?,?) ON CONFLICT(merchant) DO "
                                 "UPDATE SET category=excluded.category", (row["merchant"], category, self._clock()))
                for other in self._db.execute("SELECT id, merchant FROM transactions WHERE id!=? AND "
                                              "category_source!='manual'", (txn_id,)).fetchall():
                    if self._rule_matches(row["merchant"], other["merchant"]):
                        self._db.execute("UPDATE transactions SET category=?, category_source='rule' WHERE id=?",
                                         (category, other["id"]))
                        changed += 1
            return changed

    # ---- writing ----------------------------------------------------------------------------------
    def add_batch(self, account_id: int, rows: Iterable[tuple[str, int, str]], source: str = "csv") -> BatchResult:
        """rows: (YYYY-MM-DD, signed paise, description). The same batch again adds nothing; two genuinely equal
        rows in one batch (two coffees) are kept as two."""
        result, seen = BatchResult(), {}
        with self._lock, self._db:
            for day, minor, description in rows:
                description = " ".join(str(description).split())[:200]
                key = (day, minor, description.upper())
                seen[key] = seen.get(key, 0) + 1
                digest = hashlib.sha1(f"{account_id}|{day}|{minor}|{description.upper()}|{seen[key]}".encode()
                                      ).hexdigest()
                category, how = self.categorize(description)
                cur = self._db.execute(
                    "INSERT OR IGNORE INTO transactions(account_id,day,amount_minor,description,merchant,category,"
                    "category_source,source,ext_hash,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (account_id, day, int(minor), description, merchant_key(description), category, how, source,
                     digest, self._clock()))
                if cur.rowcount:
                    result.added += 1
                    result.ids.append(cur.lastrowid)
                else:
                    result.duplicates += 1
        return result

    def add_manual(self, account_id: int, day: str, minor: int, description: str, category: str = "") -> int:
        """A quick add; always kept (a second identical coffee is a second coffee)."""
        description = " ".join(description.split())[:200]
        cat, how = (category.lower(), "manual") if category else self.categorize(description)
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO transactions(account_id,day,amount_minor,description,merchant,category,category_source,"
                "source,ext_hash,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (account_id, day, int(minor), description, merchant_key(description), cat, how, "manual",
                 secrets.token_hex(8), self._clock()))
            return cur.lastrowid

    # ---- reading ----------------------------------------------------------------------------------
    def transactions(self, since: str | None = None, until: str | None = None, category: str | None = None,
                     limit: int = 100000) -> list[dict]:
        sql, args = "SELECT t.*, a.name AS account FROM transactions t JOIN accounts a ON a.id=t.account_id WHERE 1=1", []
        if since:
            sql += " AND t.day >= ?"
            args.append(since)
        if until:
            sql += " AND t.day <= ?"
            args.append(until)
        if category is not None:
            sql += " AND t.category = ?"
            args.append(category)
        sql += " ORDER BY t.day, t.id LIMIT ?"
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, (*args, limit))]

    def last_transaction_id(self) -> int | None:
        with self._lock:
            row = self._db.execute("SELECT id FROM transactions ORDER BY id DESC LIMIT 1").fetchone()
        return row["id"] if row else None

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

    def delete_all(self) -> int:
        with self._lock, self._db:
            n = self._db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            self._db.execute("DELETE FROM transactions")
            return n

    def add_rule(self, merchant_text: str, category: str) -> int:
        """Teach a merchant without a transaction ("anything from Zomato is food"); older unfixed rows follow."""
        key = merchant_key(merchant_text)
        category = " ".join(category.lower().split())[:30]
        if not key or not category:
            raise ValueError("say which merchant and which category")
        with self._lock, self._db:
            self._db.execute("INSERT INTO rules(merchant,category,created) VALUES(?,?,?) ON CONFLICT(merchant) DO "
                             "UPDATE SET category=excluded.category", (key, category, self._clock()))
            changed = 0
            for row in self._db.execute("SELECT id, merchant FROM transactions WHERE category_source!='manual'"
                                        ).fetchall():
                if self._rule_matches(key, row["merchant"]):
                    self._db.execute("UPDATE transactions SET category=?, category_source='rule' WHERE id=?",
                                     (category, row["id"]))
                    changed += 1
            return changed

    # ---- budgets, goals, bills ----------------------------------------------------------------------
    def set_budget(self, category: str, limit_minor: int) -> None:
        category = " ".join(category.lower().split())[:30]
        if limit_minor <= 0:
            raise ValueError("a budget must be more than zero")
        with self._lock, self._db:
            self._db.execute("INSERT INTO budgets(category,limit_minor) VALUES(?,?) ON CONFLICT(category) DO UPDATE "
                             "SET limit_minor=excluded.limit_minor", (category, int(limit_minor)))

    def budgets(self) -> dict[str, int]:
        with self._lock:
            return {r["category"]: r["limit_minor"] for r in self._db.execute("SELECT * FROM budgets")}

    def add_goal(self, name: str, target_minor: int, due: str = "") -> int:
        if target_minor <= 0:
            raise ValueError("a goal needs a target above zero")
        with self._lock, self._db:
            self._db.execute("INSERT OR IGNORE INTO goals(name,target_minor,due) VALUES(?,?,?)",
                             (" ".join(name.split())[:60], int(target_minor), due))
            return self._db.execute("SELECT id FROM goals WHERE name=?", (" ".join(name.split())[:60],)).fetchone()[0]

    def contribute(self, goal_id: int, minor: int) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE goals SET saved_minor = MAX(0, saved_minor + ?) WHERE id=?", (int(minor), goal_id))

    def goals(self, today=None) -> list[dict]:
        with self._lock:
            rows = [dict(r) for r in self._db.execute("SELECT * FROM goals ORDER BY id")]
        for g in rows:
            g["pct"] = round(100 * g["saved_minor"] / g["target_minor"]) if g["target_minor"] else 0
            g["monthly_needed_minor"] = None
            if g["due"] and today is not None:
                y, m, d = (int(x) for x in g["due"].split("-"))
                months = (y - today.year) * 12 + (m - today.month) - (1 if d < today.day else 0)
                left = max(0, g["target_minor"] - g["saved_minor"])
                g["monthly_needed_minor"] = -(-left // max(1, months))
        return rows

    def add_bill(self, name: str, amount_minor: int, due_day: int, category: str = "bills") -> int:
        if not 1 <= due_day <= 31:
            raise ValueError("a bill is due on a day from 1 to 31")
        with self._lock, self._db:
            self._db.execute("INSERT INTO bills(name,amount_minor,due_day,category) VALUES(?,?,?,?) ON CONFLICT(name) "
                             "DO UPDATE SET amount_minor=excluded.amount_minor, due_day=excluded.due_day",
                             (" ".join(name.split())[:60], int(amount_minor), due_day, category))
            return self._db.execute("SELECT id FROM bills WHERE name=?", (" ".join(name.split())[:60],)).fetchone()[0]

    def bills(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute("SELECT * FROM bills ORDER BY due_day, id")]

    # ---- exports ----------------------------------------------------------------------------------
    def export_csv(self) -> str:
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        w.writerow(["date", "account", "amount", "currency", "description", "category"])
        for t in self.transactions():
            w.writerow([t["day"], t["account"], money(t["amount_minor"]), CURRENCY, t["description"], t["category"]])
        return out.getvalue()

    def export_beancount(self) -> str:
        lines = [f"; exported by Minty ({CURRENCY})"]
        for a in self.accounts():
            lines.append(f"2000-01-01 open Assets:{_acct(a['name'])} {CURRENCY}")
        for t in self.transactions():
            amount = t["amount_minor"]
            other = ("Income:" if amount > 0 else "Expenses:") + (t["category"].capitalize() or "Uncategorised")
            desc = t["description"].replace('"', "'")
            lines += [f'{t["day"]} * "{desc}"', f"  {other}  {money(-amount)} {CURRENCY}",
                      f"  Assets:{_acct(t['account'])}  {money(amount)} {CURRENCY}", ""]
        return "\n".join(lines)


def _acct(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-") or "Account"
