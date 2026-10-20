"""Money from the phone: typed spends, forwarded bank SMS, statement files, corrections (LF-12, LF-13)."""
from datetime import date

from tests.test_finance_import import SINGLE
from tests.test_telegram_gateway import OWNER, build
from minty_core.finance import Ledger
from minty_core.finance_commands import FinanceCommands
from minty_core.secretscan import SecretScanner
from minty_core.telegram.finance import FinanceTelegram
from minty_core.telegram.intake import Intake

TODAY = date(2026, 10, 15)


class Recorder:
    history: list = []

    def __init__(self):
        self.calls = []

    async def respond(self, text, **kw):
        self.calls.append((text, kw))
        yield "ok"


def setup(tmp_path, enabled=True, private=False):
    agent = Recorder()
    gw, fake, store, _ = build(tmp_path, agent=agent, private=private)
    data = store.config.model_dump(mode="json")
    data["finance"]["enabled"] = enabled
    store.update(data)
    ledger = Ledger(tmp_path / "finance.db", clock=lambda: 1_800_000_000.0)
    FinanceTelegram(gw, lambda: ledger, today=lambda: TODAY).install()
    Intake(gw, tmp_path / "inbox", SecretScanner(), ocr=None).install()
    return gw, fake, agent, ledger


async def test_a_typed_spend_is_recorded_without_asking_a_model(tmp_path):
    gw, fake, agent, ledger = setup(tmp_path)
    await gw.handle_update(fake.say(OWNER, "spent 250 on zomato lunch"))
    assert agent.calls == [] and ledger.transactions()[0]["amount_minor"] == -25000
    assert "250.00" in fake.texts(OWNER)[-1] and "food" in fake.texts(OWNER)[-1]


async def test_ordinary_chat_still_reaches_the_assistant(tmp_path):
    gw, fake, agent, ledger = setup(tmp_path)
    await gw.handle_update(fake.say(OWNER, "what's the weather like"))
    assert agent.calls and ledger.count() == 0


async def test_with_finance_off_nothing_is_recorded_and_it_goes_to_the_assistant(tmp_path):
    gw, fake, agent, ledger = setup(tmp_path, enabled=False)
    await gw.handle_update(fake.say(OWNER, "spent 250 on lunch"))
    assert ledger.count() == 0 and agent.calls


async def test_a_forwarded_bank_sms_becomes_a_transaction_and_is_never_shown_to_a_model(tmp_path):
    gw, fake, agent, ledger = setup(tmp_path)
    sms = "Rs.1,234.50 debited from A/c XX1234 on 12-10-26 and credited to VPA zomato@okaxis. UPI Ref 412345678901."
    await gw.handle_update(fake.say(OWNER, sms, forward_origin={"type": "hidden_user", "sender_user_name": "HDFCBK"}))
    assert agent.calls == []
    t = ledger.transactions()[0]
    assert t["amount_minor"] == -123450 and t["day"] == "2026-10-12" and "412345678901" not in t["description"]
    assert "1234.50" in fake.texts(OWNER)[-1]


async def test_a_forwarded_non_bank_message_takes_the_normal_untrusted_path(tmp_path):
    gw, fake, agent, ledger = setup(tmp_path)
    await gw.handle_update(fake.say(OWNER, "Dear parents, the trip is on Friday.",
                                    forward_origin={"type": "user", "sender_user": {"first_name": "Teacher"}}))
    assert agent.calls and agent.calls[0][1].get("untrusted") and ledger.count() == 0


async def test_a_statement_file_is_imported_locally_and_a_re_send_adds_nothing(tmp_path):
    gw, fake, agent, ledger = setup(tmp_path)
    fake.add_file("d1", SINGLE.encode(), "documents/d1.bin")
    doc = {"file_id": "d1", "file_name": "october.csv", "mime_type": "text/csv", "file_size": len(SINGLE)}
    await gw.handle_update(fake.say(OWNER, "", document=doc))
    assert agent.calls == [] and ledger.count() == 3 and "Added 3" in fake.texts(OWNER)[-1]
    await gw.handle_update(fake.say(OWNER, "", document=doc))
    assert ledger.count() == 3 and "3 already there" in fake.texts(OWNER)[-1]


async def test_a_shopping_list_csv_is_not_a_statement(tmp_path):
    gw, fake, agent, ledger = setup(tmp_path)
    data = b"item,price\nmilk,30\n"
    fake.add_file("d2", data, "documents/d2.bin")
    await gw.handle_update(fake.say(OWNER, "", document={"file_id": "d2", "file_name": "list.csv",
                                                        "mime_type": "text/csv", "file_size": len(data)}))
    assert ledger.count() == 0 and agent.calls  # the normal file reader took it


async def test_correcting_by_phrase_teaches_the_merchant(tmp_path):
    gw, fake, agent, ledger = setup(tmp_path)
    acct = ledger.add_account("Cash")
    ledger.add_batch(acct, [("2026-10-01", -30000, "UPI/RAVI KIRANA/1"), ("2026-10-09", -21000, "UPI/RAVI KIRANA/2")])
    await gw.handle_update(fake.say(OWNER, "categorize ravi kirana as groceries"))
    assert {t["category"] for t in ledger.transactions()} == {"groceries"} and agent.calls == []
    assert "2 transactions" in fake.texts(OWNER)[-1]


def test_the_voice_phrases_work_without_telegram(tmp_path):
    ledger = Ledger(tmp_path / "f.db", clock=lambda: 1_800_000_000.0)
    acct = ledger.add_account("Cash")
    ledger.add_batch(acct, [("2026-10-01", -30000, "UPI/KIRANA SHOP/1")])
    cmds = FinanceCommands(ledger, today=lambda: TODAY)
    assert "1 transaction" in cmds.handle("mark kirana shop as groceries")
    assert cmds.handle("what time is it") is None
    cmds.handle("spent 40 on chai")
    assert "snacks" in cmds.handle("that was snacks") and ledger.transactions()[-1]["category"] == "snacks"
