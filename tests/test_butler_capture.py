"""1051 brain-dump sorter, 1057 photo-to-notes filing, 830 mailbox triage."""
import imaplib
from datetime import datetime

import pytest

from minty_core.butler.braindump import apply, sort
from minty_core.butler.domain import Butler
from minty_core.butler.domains import all_domains
from minty_core.butler.filing import plan_filing
from minty_core.butler.store import ButlerStore
from minty_core.butler.triage import classify, render, triage
from tests.plugin_helpers import make_host

NOW = datetime(2026, 10, 15, 10, 0)


# ---- 1051 ------------------------------------------------------------------------------------------------
DUMP = ("I need to call the dentist tomorrow. Maybe a robot that waters the plants. Waiting for Ravi to send the "
        "form. Buy a ribbon cable and a charger. Remind me to submit the form on friday; the sky was nice today")


def test_a_ramble_is_split_into_typed_proposals():
    c = sort(DUMP)
    assert [(x.n, x.kind) for x in c] == [(1, "todo"), (2, "idea"), (3, "waiting"), (4, "todo"), (5, "todo"),
                                          (6, "note")]
    assert c[0].text == "call the dentist" and c[0].when == "tomorrow"
    assert c[2].who == "Ravi" and c[2].text == "send the form"
    assert c[4].text == "submit the form" and c[4].when == "on friday"
    assert c[3].text == "Buy a ribbon cable and a charger"


def test_empty_and_huge_input_are_bounded():
    assert sort("   ") == []
    assert len(sort(". ".join(f"I need to do thing {i}" for i in range(100)))) == 20


@pytest.fixture
def butler(tmp_path):
    t = {"v": NOW.timestamp()}
    return Butler(ButlerStore(tmp_path / "b.db", clock=lambda: t["v"]), all_domains(), clock=lambda: t["v"])


def test_only_the_picked_proposals_are_created(butler):
    c = sort(DUMP)
    saved = []
    out = apply(butler, c, [1, 3], note_sink=lambda title, text: saved.append((title, text)))
    assert len(butler.list("tasks")) == 1 and butler.list("tasks")[0].name == "call the dentist"
    assert butler.list("waiting")[0].name == "Ravi: send the form"
    assert saved == [] and "2 added" in out


def test_ideas_and_notes_go_to_the_note_sink_when_there_is_one_else_the_ideas_list(butler):
    c = sort(DUMP)
    saved = []
    apply(butler, c, [2, 6], note_sink=lambda title, text: saved.append((title, text)))
    assert [t for t, _ in saved] == ["Maybe a robot that waters the plants", "the sky was nice today"]
    apply(butler, c, [2], note_sink=None)
    assert [i.name for i in butler.list("ideas")] == ["Maybe a robot that waters the plants"]


def test_apply_ignores_numbers_that_do_not_exist(butler):
    assert "0 added" in apply(butler, sort(DUMP), [99, 0, -1], note_sink=None)


# ---- 1057 ------------------------------------------------------------------------------------------------
def test_filing_names_a_document_from_the_caption_or_first_line_and_masks_ids():
    p = plan_filing("Sacred Palm School\nFee receipt\nAadhaar 2345 6789 0124\nAmount 4500", "file this", NOW)
    assert p.title == "Sacred Palm School" and "receipt" in p.tags and p.masked
    assert "2345 6789 0124" not in p.body and "[aadhaar]" in p.body
    q = plan_filing("some text", "file: Hall ticket 2026", NOW)
    assert q.title == "Hall ticket 2026" and not q.masked
    r = plan_filing("  \n  ", "save", NOW)
    assert r.title == "Photo 2026-10-15" and r.body.strip() == ""


async def test_a_captioned_photo_is_filed_into_sources_as_untrusted_and_not_sent_to_a_model(tmp_path):
    from minty_core.butler.filing import file_photo
    from minty_core.config import Config
    from minty_core.vault.kit import VaultProvider
    from tests.test_telegram_gateway import OWNER
    from tests.test_telegram_intake import photo, setup

    cfg = Config()
    cfg.vault.enabled, cfg.vault.path, cfg.vault.git = True, str(tmp_path / "brain"), False
    cfg.butler.enabled, cfg.butler.photo_filing = True, True
    provider = VaultProvider(config=lambda: cfg)
    gw, fake, agent, intake = setup(tmp_path, ocr_text="Fee receipt\nPaid 4500 on 12 Oct")

    async def hook(kind, name, text, caption, forwarded):
        return await file_photo(provider, cfg, kind, name, text, caption, forwarded, now=NOW)

    intake.file_hook = hook
    await gw.handle_update(photo(fake, caption="file this"))
    assert agent.calls == []  # no model saw it
    assert "Filed" in fake.texts(OWNER)[-1] and "note:" in fake.texts(OWNER)[-1]
    (n,) = provider.kit().vault.list("sources")
    assert n.confidence == "untrusted" and "Paid 4500" in n.body and "filed-photo" in n.tags
    # without the caption it takes the normal path
    await gw.handle_update(photo(fake, caption="what is this"))
    assert len(agent.calls) == 1


async def test_filing_is_off_unless_switched_on(tmp_path):
    from minty_core.butler.filing import file_photo
    from minty_core.config import Config
    from minty_core.vault.kit import VaultProvider

    cfg = Config()
    cfg.vault.enabled, cfg.vault.path, cfg.vault.git = True, str(tmp_path / "brain"), False
    out = await file_photo(VaultProvider(config=lambda: cfg), cfg, "image", "photo.jpg", "x", "file this", False)
    assert out is None


# ---- 830 -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("sender,subject,expected", [
    ("Google", "Security alert: new sign-in from Chrome on Linux", "alert"),
    ("Jio", "Your bill is ready - payment due 20 Oct", "bill"),
    ("Airtel", "Recharge due: your plan expires tomorrow", "bill"),
    ("Amazon", "Your order has been delivered", "receipt"),
    ("Zomato", "Thank you for your order #2231", "receipt"),
    ("Sam", "lunch?", "answer"),
    ("Sir", "Please confirm your attendance for the trip", "answer"),
    ("Shop", "50% off sale this weekend", "newsletter"),
    ("Substack", "This week's digest - unsubscribe anytime", "newsletter"),
    ("A friend", "photos", "other"),
])
def test_classify(sender, subject, expected):
    assert classify(sender, subject) == expected


def test_triage_counts_and_lists_what_needs_you():
    rows = [{"uid": "1", "who": "Google", "subject": "Security alert: new sign-in"},
            {"uid": "2", "who": "Jio", "subject": "Bill due 20 Oct"},
            {"uid": "3", "who": "Sam", "subject": "lunch?"},
            {"uid": "4", "who": "Shop", "subject": "50% off sale"},
            {"uid": "5", "who": "Amazon", "subject": "Your order was delivered"}]
    s = triage(rows)
    assert s.counts == {"alert": 1, "bill": 1, "receipt": 1, "answer": 1, "newsletter": 1, "other": 0}
    text = render(s)
    assert text.startswith("Mailbox triage (5 unread): 1 alert, 1 bill, 1 to answer, 1 receipt, 1 newsletter.")
    assert "Sam" in text and "Jio" in text and "Shop" not in text  # promos are counted, not listed


MAILS = {
    b"1": b"From: Shop <deals@shop.example>\r\nSubject: 50% off sale this weekend\r\nList-Unsubscribe: <m>\r\n\r\nBuy",
    b"2": b"From: School <office@school.example>\r\nSubject: Fee notice: payment due Friday\r\n\r\npay",
    b"3": b"From: Sam <sam@example.com>\r\nSubject: lunch?\r\n\r\nfree?",
}


class FakeIMAP:
    def __init__(self, host, port):
        pass

    def login(self, u, p):
        pass

    def select(self, mailbox, readonly=False):
        return "OK", [b"3"]

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [b"1 2 3"]
        uid = args[0]
        return "OK", [(b"%b (UID %b)" % (uid, uid), MAILS[uid]), b")"]

    def logout(self):
        pass


async def test_the_mail_plugin_triages_the_mailbox_read_only(tmp_path, monkeypatch):
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    host = make_host(tmp_path, plugins={"mail": {"enabled": True, "user": "me@example.com"}},
                     secrets={"plugin.mail.password": "pw"})
    assert "switched off" in await host.call("mail_triage", {})
    data = host._store.config.model_dump(mode="json")
    data["butler"]["mailbox_triage"] = True
    host._store.update(data)
    out = await host.call("mail_triage", {})
    assert "Mailbox triage (3 unread): 1 bill, 1 to answer, 1 newsletter." in out
    assert out.startswith("[untrusted e-mail content") or "[untrusted e-mail content" in out


async def test_the_plugin_sorts_a_dump_and_applies_only_the_picks(tmp_path):
    h = make_host(tmp_path / "cfg")
    data = h._store.config.model_dump(mode="json")
    data["butler"]["enabled"] = True
    data["vault"].update(enabled=True, path=str(tmp_path / "brain"), git=False)
    h._store.update(data)
    listing = await h.call("butler_sort", {"text": DUMP})
    assert "1. [to-do] call the dentist (tomorrow)" in listing and "3. [follow-up] Ravi: send the form" in listing
    out = await h.call("butler_apply", {"numbers": [1, 2, 3]})
    assert "3 added" in out
    assert [i.name for i in h._services["butler"].butler().list("tasks")] == ["call the dentist"]
    (note,) = h._services["vault"].kit().vault.list("inbox")
    assert note.title.startswith("Maybe a robot") and note.confidence == "verified" and "brain-dump" in note.tags
    assert h.get_tool("butler_apply").risk == 1 and h.get_tool("butler_sort").risk == 0


async def test_applying_without_a_recent_sort_asks_for_the_dump_again(tmp_path):
    h = make_host(tmp_path / "cfg")
    data = h._store.config.model_dump(mode="json")
    data["butler"]["enabled"] = True
    h._store.update(data)
    assert "send the brain dump again" in await h.call("butler_apply", {"numbers": [1]})
