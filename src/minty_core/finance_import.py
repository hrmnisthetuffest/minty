"""Getting records into the ledger: CSV statements (a header guess, no bank-specific code), quick add phrases,
forwarded bank SMS and alert text, and an optional PDF reader.

Nothing is fetched from a bank and no credential is asked for or kept. A PDF password is used once, in memory.
Account numbers in SMS text are never stored (the description keeps only what was paid to).
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

from minty_core.finance import Ledger, parse_money

MAX_ROWS = 100_000
BAD_ROW_LIMIT = 0.2
DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%Y-%m-%d", "%d %b %Y", "%d-%b-%Y", "%d-%b-%y",
                "%d %b %y", "%m/%d/%Y")
_DESC = ("narration", "description", "particulars", "details", "remarks", "transaction", "payee", "merchant")
_DEBIT = ("withdrawal", "debit", "paid out", "money out", "dr amount", "dr.")
_CREDIT = ("deposit", "credit", "paid in", "money in", "cr amount", "cr.")


class ImportRejected(Exception):
    """Not something this can read; the message says why."""


@dataclass
class FinanceReport:
    added: int = 0
    duplicates: int = 0
    problems: int = 0

    def text(self) -> str:
        parts = [f"Added {self.added} transaction{'s' * (self.added != 1)}"]
        if self.duplicates:
            parts.append(f"{self.duplicates} already there")
        if self.problems:
            parts.append(f"{self.problems} unreadable row{'s' * (self.problems != 1)} skipped")
        return ", ".join(parts) + "."


def _has(header: str, words: tuple[str, ...]) -> bool:
    return any(w in header for w in words)


def _header_row(rows: list[list[str]]) -> int | None:
    return next((i for i, r in enumerate(rows[:40])
                 if any("date" in c.lower() for c in r) and any(_has(c.lower(), _DESC) for c in r)), None)


def looks_like_statement(data: bytes) -> bool:
    """A text file with a header that has a date, a description and an amount (or debit/credit) column."""
    try:
        text = data[:200_000].decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    delim = ";" if first.count(";") > first.count(",") else "\t" if first.count("\t") > first.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    at = _header_row(rows)
    if at is None:
        return False
    header = [c.strip().lower() for c in rows[at]]
    return any("amount" in h or _has(h, _DEBIT + _CREDIT) for h in header)


def import_csv(ledger: Ledger, account_id: int, data: bytes) -> FinanceReport:
    if not data.strip():
        raise ImportRejected("that file is empty")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportRejected("that isn't a text file; export the statement as CSV") from None
    if "\x00" in text[:2000]:
        raise ImportRejected("that isn't a text file; export the statement as CSV")
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    delim = ";" if first.count(";") > first.count(",") else "\t" if first.count("\t") > first.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    if len(rows) > MAX_ROWS:
        raise ImportRejected("that statement has too many rows")
    head_at = _header_row(rows)
    if head_at is None:
        raise ImportRejected("I couldn't find a header row with a date and a description (narration) column")
    header = [c.strip().lower() for c in rows[head_at]]

    def find(pred) -> int | None:
        return next((i for i, h in enumerate(header) if pred(h)), None)

    date_i = find(lambda h: h in ("txn date", "transaction date", "date", "posting date")) or find(
        lambda h: "date" in h and "value" not in h)
    if date_i is None:
        date_i = find(lambda h: "date" in h)
    desc_i = find(lambda h: _has(h, _DESC))
    debit_i = find(lambda h: _has(h, _DEBIT) and "balance" not in h)
    credit_i = find(lambda h: _has(h, _CREDIT) and "balance" not in h)
    amount_i = find(lambda h: "amount" in h and not _has(h, _DEBIT + _CREDIT) and "balance" not in h)
    type_i = find(lambda h: h in ("type", "dr/cr", "cr/dr", "debit/credit", "txn type", "transaction type"))
    if amount_i is None and debit_i is None and credit_i is None:
        raise ImportRejected("I couldn't find an amount column (amount, or withdrawal and deposit)")

    def cell(r, i):
        return r[i].strip() if i is not None and i < len(r) else ""

    body = []  # (date text, description, amount cells...)
    for r in rows[head_at + 1:]:
        if not any(c.strip() for c in r):
            continue
        cells = (cell(r, amount_i), cell(r, debit_i), cell(r, credit_i))
        body.append((cell(r, date_i), cell(r, desc_i), *cells, cell(r, type_i)))
    dated = [b[0] for b in body if any(b[2:5])]
    fmt = None
    for candidate in DATE_FORMATS:
        ok = 0
        for d in dated:
            try:
                datetime.strptime(d, candidate)
                ok += 1
            except ValueError:
                pass
        if dated and ok / len(dated) >= 0.9:
            fmt = candidate
            break
    out, problems = [], 0
    for d, desc, amount, debit, credit, kind in body:
        if not any((amount, debit, credit)):
            continue  # an opening/closing balance line or a blank: not a transaction
        try:
            if fmt is None:
                raise ValueError("no date format")
            day = datetime.strptime(d, fmt).strftime("%Y-%m-%d")
            if amount:
                minor = parse_money(amount)
                k = kind.lower()
                if k in ("dr", "debit", "d", "withdrawal"):
                    minor = -abs(minor)
                elif k in ("cr", "credit", "c", "deposit"):
                    minor = abs(minor)
            else:
                minor = (parse_money(credit) if credit else 0) - (abs(parse_money(debit)) if debit else 0)
            out.append((day, minor, desc or "(no description)"))
        except ValueError:
            problems += 1
    if problems and (not out or problems / (problems + len(out)) > BAD_ROW_LIMIT):
        raise ImportRejected(f"{problems} of {problems + len(out)} rows were unreadable, so I stored nothing")
    if not out:
        raise ImportRejected("I found the columns but no transactions in that file")
    batch = ledger.add_batch(account_id, out, source="csv")
    return FinanceReport(batch.added, batch.duplicates, problems)


# ---- quick add ---------------------------------------------------------------------------------------------
@dataclass
class Quick:
    minor: int
    description: str
    day: str


_AMT = r"(?:rs\.?\s*|₹\s*|inr\s*)?(\d[\d,]*(?:\.\d{1,2})?)\s*(?:rupees?|rs\b)?"
_DAY = r"(?:\s+(today|yesterday))?"
_SPENT = re.compile(rf"^(?:i\s+)?(?:spent|paid|gave|lent|donated)\s+{_AMT}\s*(?:on|for|to|at)?\s*(.*?){_DAY}\s*$", re.I)
_GOT = re.compile(rf"^(?:i\s+)?(?:received|got|earned|was given)\s+{_AMT}\s*(from|as|for)?\s*(.*?){_DAY}\s*$", re.I)
_BOUGHT = re.compile(rf"^(?:i\s+)?bought\s+(.+?)\s+for\s+{_AMT}{_DAY}\s*$", re.I)


def parse_quick(text: str, today: date) -> Quick | None:
    t = " ".join((text or "").strip().rstrip(".!").split())
    when = today

    def day_of(word: str | None) -> str:
        return (today - timedelta(days=1) if (word or "").lower() == "yesterday" else when).isoformat()

    m = _SPENT.match(t)
    if m:
        desc = m.group(2).strip()
        return Quick(-parse_money(m.group(1)), desc or "spent", day_of(m.group(3)))
    m = _BOUGHT.match(t)
    if m:
        return Quick(-parse_money(m.group(2)), m.group(1).strip(), day_of(m.group(3)))
    m = _GOT.match(t)
    if m:
        who = m.group(3).strip()
        desc = f"from {who}" if m.group(2) and m.group(2).lower() == "from" else who or "received"
        return Quick(parse_money(m.group(1)), desc, day_of(m.group(4)))
    return None


# ---- SMS and alert text ---------------------------------------------------------------------------------
@dataclass
class Parsed:
    minor: int
    description: str
    day: str
    account_hint: str = ""


_SMS_AMOUNT = re.compile(r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d{1,2})?)", re.I)
_OUT = re.compile(r"\b(debited|spent|withdrawn|purchase|paid|sent)\b", re.I)
_IN = re.compile(r"\b(credited|received|deposited|refund)\b", re.I)
_SMS_DATE = re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})\b|\b(\d{1,2})[- ]([A-Za-z]{3})[- ](\d{2,4})\b")
_VPA = re.compile(r"([A-Za-z0-9._\-]{2,})@[A-Za-z]{2,}")
_INFO = re.compile(r"(?:info[:\-]|to|from|at)\s+([A-Za-z][A-Za-z0-9 &.'\-]{2,40}?)(?=\s+(?:on|ref|upi|a/c|avl|if|\.|$)|[.,]|$)",
                   re.I)
_ACCT = re.compile(r"(?:xx|x{2,}|a/c(?: no\.?)?\s*)\D{0,3}(\d{4})\b", re.I)


def parse_sms(text: str, today: date) -> Parsed | None:
    t = " ".join((text or "").split())
    if re.search(r"\botp\b", t, re.I) or not _SMS_AMOUNT.search(t):
        return None
    out, inn = _OUT.search(t), _IN.search(t)
    if not out and not inn:
        return None
    outgoing = bool(out and (not inn or out.start() < inn.start()))
    minor = parse_money(_SMS_AMOUNT.search(t).group(1))
    day = today
    d = _SMS_DATE.search(t)
    if d:
        try:
            if d.group(1):
                a, b, y = int(d.group(1)), int(d.group(2)), int(d.group(3))
                day = date(2000 + y if y < 100 else y, b, a)
            else:
                day = datetime.strptime(f"{d.group(4)} {d.group(5)} {d.group(6)}",
                                        "%d %b %y" if len(d.group(6)) == 2 else "%d %b %Y").date()
        except ValueError:
            day = today
    vpa = _VPA.search(t)
    info = _INFO.search(t)
    who = (vpa.group(1) if vpa else info.group(1).strip() if info else "") or "bank transaction"
    who = re.sub(r"\d{6,}", "", who).strip(" .-")
    acct = _ACCT.search(t)
    return Parsed(-minor if outgoing else minor, who[:60], day.isoformat(), f"XX{acct.group(1)}" if acct else "")


# ---- PDF (optional) ---------------------------------------------------------------------------------------
_PDF_LINE = re.compile(r"^(\d{2}[/-]\d{2}[/-]\d{2,4})\s+(.+?)\s+([\d,]+\.\d{2})\s*(Dr|Cr|DR|CR)?(?:\s+[\d,]+\.\d{2})?$")


def pdf_extract(data: bytes, password: str) -> str:
    """Text of a PDF with pypdf (an optional package). Raises PermissionError for a wrong password."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportRejected("reading PDF statements needs pypdf (pip install -e '.[documents]')") from None
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted and not reader.decrypt(password or ""):
        raise PermissionError("wrong password")
    return "\n".join((p.extract_text() or "") for p in reader.pages[:60])


def import_pdf_text(ledger: Ledger, account_id: int, data: bytes, password: str,
                    extractor: Callable[[bytes, str], str] = pdf_extract) -> FinanceReport:
    try:
        text = extractor(data, password)
    except PermissionError:
        raise ImportRejected("that PDF needs its password and the one given was wrong") from None
    except ImportRejected:
        raise
    except Exception:  # noqa: BLE001 — a damaged PDF is "unreadable", not a crash
        raise ImportRejected("I couldn't read that PDF") from None
    rows = []
    for line in text.splitlines():
        m = _PDF_LINE.match(line.strip())
        if not m:
            continue
        for fmt in DATE_FORMATS:
            try:
                day = datetime.strptime(m.group(1), fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                continue
        else:
            continue
        minor = parse_money(m.group(3))
        rows.append((day, -minor if (m.group(4) or "").lower() == "dr" else minor, m.group(2)))
    if not rows:
        raise ImportRejected("I found no transactions in that PDF")
    batch = ledger.add_batch(account_id, rows, source="pdf")
    return FinanceReport(batch.added, batch.duplicates, 0)
