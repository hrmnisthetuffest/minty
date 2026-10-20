"""Getting money records in: CSV statements, quick add, forwarded SMS and alert text, optional PDF (LF-12)."""
from datetime import date

import pytest

from minty_core.finance import Ledger
from minty_core.finance_import import (ImportRejected, import_csv, import_pdf_text, parse_quick, parse_sms)

TODAY = date(2026, 10, 15)

SINGLE = (
    "Date,Narration,Amount,Balance\n"
    "01/10/2026,UPI/ZOMATO/412345678901/okaxis,-245.00,10755.00\n"
    "02/10/2026,SALARY OCT,50000.00,60755.00\n"
    "03/10/2026,POS 4521 BIGBASKET,-1320.50,59434.50\n")
SPLIT_WITH_PREAMBLE = (
    "HDFC BANK Ltd.\nAccount Statement for A/c XX1234\nFrom 01/10/2026 To 31/10/2026\n\n"
    "Txn Date,Value Date,Particulars,Withdrawal Amt.,Deposit Amt.,Closing Balance\n"
    "01/10/26,01/10/26,UPI-SWIGGY-9876,312.00,,9000.00\n"
    "05/10/26,05/10/26,NEFT-RAVI-POCKET MONEY,,2000.00,11000.00\n"
    "Opening balance 9312.00,,,,,\n")
DRCR = (
    "Date,Description,Amount,Type\n2026-10-04,Netflix.com,649.00,DR\n2026-10-05,Refund Amazon,499.00,CR\n")


def led(tmp_path):
    ledger = Ledger(tmp_path / "f.db", clock=lambda: 1_800_000_000.0)
    return ledger, ledger.add_account("Main")


def test_a_single_signed_amount_column(tmp_path):
    ledger, acct = led(tmp_path)
    report = import_csv(ledger, acct, SINGLE.encode())
    assert (report.added, report.duplicates, report.problems) == (3, 0, 0)
    rows = ledger.transactions()
    assert [t["amount_minor"] for t in rows] == [-24500, 5000000, -132050]
    assert [t["category"] for t in rows] == ["food", "income", "groceries"]
    assert rows[0]["day"] == "2026-10-01"


def test_debit_and_credit_columns_after_a_preamble_with_two_digit_years(tmp_path):
    ledger, acct = led(tmp_path)
    report = import_csv(ledger, acct, SPLIT_WITH_PREAMBLE.encode())
    assert report.added == 2 and report.problems == 0  # the "Opening balance" line is not a transaction
    assert [t["amount_minor"] for t in ledger.transactions()] == [-31200, 200000]
    assert ledger.transactions()[0]["day"] == "2026-10-01"


def test_a_type_column_decides_the_sign(tmp_path):
    ledger, acct = led(tmp_path)
    import_csv(ledger, acct, DRCR.encode())
    assert [t["amount_minor"] for t in ledger.transactions()] == [-64900, 49900]


def test_the_same_statement_twice_adds_nothing_and_an_overlapping_one_adds_only_the_new_rows(tmp_path):
    ledger, acct = led(tmp_path)
    import_csv(ledger, acct, SINGLE.encode())
    again = import_csv(ledger, acct, SINGLE.encode())
    assert (again.added, again.duplicates) == (0, 3)
    newer = SINGLE + "04/10/2026,UBER TRIP,-180.00,59254.50\n"
    part = import_csv(ledger, acct, newer.encode())
    assert (part.added, part.duplicates) == (1, 3) and ledger.count() == 4


@pytest.mark.parametrize("data, why", [
    (b"just some words\nnot a statement\n", "couldn't find"),
    (b"Date,Narration\n01/10/2026,hello\n", "amount"),
    (b"", "empty"),
    (b"\x00\x01\xff" * 30, "text"),
])
def test_what_is_not_a_statement_is_refused_with_a_reason(tmp_path, data, why):
    ledger, acct = led(tmp_path)
    with pytest.raises(ImportRejected) as e:
        import_csv(ledger, acct, data)
    assert why in str(e.value).lower() and ledger.count() == 0


def test_a_mostly_unreadable_statement_is_refused_whole(tmp_path):
    ledger, acct = led(tmp_path)
    bad = "Date,Narration,Amount\n" + "\n".join(f"not-a-date,thing {i},abc" for i in range(8)) + \
          "\n01/10/2026,ok,-10.00\n"
    with pytest.raises(ImportRejected) as e:
        import_csv(ledger, acct, bad.encode())
    assert "unreadable" in str(e.value) and ledger.count() == 0


@pytest.mark.parametrize("text, minor, desc, day", [
    ("spent 200 on lunch", -20000, "lunch", "2026-10-15"),
    ("paid 1200 for school fees", -120000, "school fees", "2026-10-15"),
    ("received 500 from dad", 50000, "dad", "2026-10-15"),
    ("got 2000 pocket money", 200000, "pocket money", "2026-10-15"),
    ("spent Rs 49.50 on chai yesterday", -4950, "chai", "2026-10-14"),
    ("bought pens for 60", -6000, "pens", "2026-10-15"),
])
def test_quick_add_phrases(text, minor, desc, day):
    q = parse_quick(text, TODAY)
    assert q is not None and q.minor == minor and desc in q.description and q.day == day


@pytest.mark.parametrize("text", ["what time is it", "spent some time on it", "remind me to pay the fee"])
def test_things_that_are_not_a_spend_are_left_alone(text):
    assert parse_quick(text, TODAY) is None


def test_a_bank_sms_becomes_a_transaction_with_the_account_number_masked():
    sms = ("Rs.1,234.50 debited from A/c XX1234 on 12-10-26 and credited to VPA zomato@okaxis. "
           "UPI Ref 412345678901. If not you, call 18001234567")
    p = parse_sms(sms, TODAY)
    assert p is not None and p.minor == -123450 and p.day == "2026-10-12"
    assert "412345678901" not in p.description and "18001234567" not in p.description
    assert "zomato" in p.description.lower()
    credit = parse_sms("INR 5,000.00 credited to your a/c no. 50100123456789 on 13/10/2026 by NEFT from RAVI", TODAY)
    assert credit.minor == 500000 and "50100123456789" not in credit.description and credit.day == "2026-10-13"


@pytest.mark.parametrize("text", ["Your OTP is 482913. Do not share it.", "Get 50% off on your next order!",
                                  "Dear customer, your statement is ready."])
def test_otps_and_promotions_are_not_transactions(text):
    assert parse_sms(text, TODAY) is None


def test_a_pdf_statement_needs_its_password_and_never_keeps_it(tmp_path):
    ledger, acct = led(tmp_path)
    lines = ("01/10/2026 UPI-ZOMATO-4123 245.00 Dr 10755.00\n02/10/2026 SALARY OCT 50000.00 Cr 60755.00\n"
             "Statement summary page 1 of 1\n")

    def extractor(data, password):
        if password != "abc123":
            raise PermissionError("wrong password")
        return lines

    with pytest.raises(ImportRejected) as e:
        import_pdf_text(ledger, acct, b"%PDF", "nope", extractor=extractor)
    assert "password" in str(e.value).lower()
    report = import_pdf_text(ledger, acct, b"%PDF", "abc123", extractor=extractor)
    assert report.added == 2 and [t["amount_minor"] for t in ledger.transactions()] == [-24500, 5000000]
    assert "abc123" not in repr(report.__dict__)
