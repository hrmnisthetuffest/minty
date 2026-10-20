"""Approvals from the phone: bound buttons, stale buttons, class 3 stays on the laptop (W3-03)."""
from tests.test_telegram_gateway import OWNER, STRANGER, build
from minty_core.approvals import ApprovalStore
from minty_core.telegram.approvals import LAPTOP_ONLY, STALE, ApprovalBridge, Passphrase, button_data


def setup(tmp_path, class3=False):
    gw, fake, store, _ = build(tmp_path)
    if class3:
        data = store.config.model_dump(mode="json")
        data["telegram"]["class3_passphrase"] = True
        store.update(data)
    approvals = ApprovalStore(tmp_path / "approvals.sqlite")
    phrase = Passphrase(tmp_path / "passphrase.json")
    bridge = ApprovalBridge(gw, approvals, phrase)
    return gw, fake, approvals, bridge, phrase


async def test_a_job_waiting_for_a_yes_reaches_the_phone_with_two_buttons(tmp_path):
    gw, fake, approvals, bridge, _ = setup(tmp_path)
    ap = approvals.request("files_delete", {"path": "old"}, "Delete the old notes?", risk=2, job_id=7)
    approvals.request("mail_send", {"to": "x"}, "Said out loud on the laptop", risk=2)  # no job: not offered
    assert await bridge.offer_new() == 1
    sent = fake.sent[0]
    assert "Approval needed: Delete the old notes?" in sent["text"] and sent["text"].startswith("[laptop]")
    buttons = [b["callback_data"] for b in sent["reply_markup"]["inline_keyboard"][0]]
    assert buttons == [button_data(ap, True), button_data(ap, False)]
    assert await bridge.offer_new() == 0  # not twice


async def test_approve_and_deny(tmp_path):
    gw, fake, approvals, bridge, _ = setup(tmp_path)
    a = approvals.request("files_delete", {"p": "a"}, "Delete a?", risk=2, job_id=1)
    b = approvals.request("files_delete", {"p": "b"}, "Delete b?", risk=2, job_id=2)
    await gw.handle_update(fake.press(OWNER, button_data(a, True)))
    await gw.handle_update(fake.press(OWNER, button_data(b, False)))
    assert approvals.get(a.id).status == "approved" and approvals.get(b.id).status == "denied"
    assert [x["text"] for x in fake.answers] == ["Approved.", "Denied."]
    assert approvals.consume(a.id, "files_delete", {"p": "a"}) is True


async def test_a_button_for_one_action_cannot_approve_another(tmp_path):
    gw, fake, approvals, bridge, _ = setup(tmp_path)
    a = approvals.request("mail_send", {"to": "teacher"}, "Send to the teacher?", risk=2, job_id=1)
    b = approvals.request("mail_send", {"to": "evil"}, "Send to someone else?", risk=2, job_id=2)
    forged = f"ap:{a.id}:{b.hash[:8]}:y"  # A's id with B's hash
    await gw.handle_update(fake.press(OWNER, forged))
    assert approvals.get(a.id).status == "pending" and fake.answers[-1]["text"] == STALE
    await gw.handle_update(fake.press(OWNER, f"ap:{a.id}:{a.hash[:4]}:y"))  # a short prefix is no good either
    assert approvals.get(a.id).status == "pending"
    assert approvals.consume(a.id, "mail_send", {"to": "evil"}) is False  # and the hash still binds the action


async def test_a_stale_or_answered_button_says_so_and_strangers_are_ignored(tmp_path):
    gw, fake, approvals, bridge, _ = setup(tmp_path)
    a = approvals.request("files_delete", {"p": "a"}, "Delete a?", risk=2, job_id=1)
    await gw.handle_update(fake.press(OWNER, button_data(a, True)))
    await gw.handle_update(fake.press(OWNER, button_data(a, True)))  # pressed twice
    assert fake.answers[-1]["text"] == STALE
    c = approvals.request("files_delete", {"p": "c"}, "Delete c?", risk=2, job_id=3)
    await gw.handle_update(fake.press(STRANGER, button_data(c, True)))
    assert approvals.get(c.id).status == "pending" and len(fake.answers) == 2  # no answer to the stranger


async def test_class_three_cannot_be_approved_from_the_phone_by_default(tmp_path):
    gw, fake, approvals, bridge, _ = setup(tmp_path)
    risky = approvals.request("installer_install", {"package": "x"}, "Install x?", risk=3, job_id=1)
    await gw.handle_update(fake.press(OWNER, button_data(risky, True)))
    assert approvals.get(risky.id).status == "pending"
    assert fake.answers[-1]["text"] == LAPTOP_ONLY and fake.answers[-1]["show_alert"] is True
    await gw.handle_update(fake.press(OWNER, button_data(risky, False)))  # saying no is always allowed
    assert approvals.get(risky.id).status == "denied"


async def test_with_the_passphrase_option_class_three_needs_the_words(tmp_path):
    gw, fake, approvals, bridge, phrase = setup(tmp_path, class3=True)
    phrase.set("blue horse battery")
    assert "blue horse" not in (tmp_path / "passphrase.json").read_text()
    risky = approvals.request("installer_install", {"package": "x"}, "Install x?", risk=3, job_id=1)
    await gw.handle_update(fake.press(OWNER, button_data(risky, True)))
    assert approvals.get(risky.id).status == "pending" and "passphrase" in fake.texts(OWNER)[-1]
    await gw.handle_update(fake.say(OWNER, "wrong words"))
    assert approvals.get(risky.id).status == "pending" and "wasn't it" in fake.texts(OWNER)[-1]
    await gw.handle_update(fake.press(OWNER, button_data(risky, True)))
    await gw.handle_update(fake.say(OWNER, "blue horse battery"))
    assert approvals.get(risky.id).status == "approved" and fake.texts(OWNER)[-1] == "[laptop] Approved."
    assert gw.agent.asked == []  # the passphrase never reached the assistant


def test_the_passphrase_locks_after_three_wrong_tries(tmp_path):
    clock = {"t": 0.0}
    p = Passphrase(tmp_path / "p.json", lambda: clock["t"])
    p.set("right words")
    assert [p.check("a"), p.check("b"), p.check("c")] == [False, False, False]
    assert p.check("right words") is False  # locked for ten minutes
    clock["t"] += 601
    assert p.check("right words") is True
