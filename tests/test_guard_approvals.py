"""The safety gate records each question as an approval, and a yes only covers its own action (W1-02)."""
from types import SimpleNamespace

from minty_core.approvals import ApprovalStore
from minty_core.config import SafetyConfig
from minty_core.policy import R3, Guard


class Tool:
    name = "mail_send"
    risk = R3
    risky = False
    confirm_when = None
    egress = None
    describe = None


def make(tmp_path, **safety):
    store = SimpleNamespace(config=SimpleNamespace(safety=SafetyConfig(**safety)))
    approvals = ApprovalStore(tmp_path / "approvals.sqlite")
    return Guard(store, approvals=approvals), approvals


async def test_a_question_becomes_an_approved_and_used_record(tmp_path):
    guard, approvals = make(tmp_path)
    asked = []

    async def confirm(question):
        asked.append(question)
        return True

    d = await guard.authorize(Tool(), "mail_send", {"to": "a@example.com", "body": "hi"}, confirm)
    assert d.allowed and d.asked and d.approval_id is not None and asked
    rec = approvals.get(d.approval_id)
    assert rec.status == "used" and rec.tool == "mail_send" and rec.risk == R3


async def test_a_no_is_recorded_as_denied(tmp_path):
    guard, approvals = make(tmp_path)

    async def confirm(_q):
        return False

    d = await guard.authorize(Tool(), "mail_send", {"to": "a@example.com"}, confirm)
    assert not d.allowed and approvals.get(d.approval_id).status == "denied"


async def test_a_confirmation_that_fails_is_a_denial(tmp_path):
    guard, approvals = make(tmp_path)

    async def confirm(_q):
        raise RuntimeError("microphone gone")

    d = await guard.authorize(Tool(), "mail_send", {"to": "a@example.com"}, confirm)
    assert not d.allowed and approvals.get(d.approval_id).status == "denied"


async def test_with_nobody_to_ask_nothing_is_allowed(tmp_path):
    guard, approvals = make(tmp_path)
    d = await guard.authorize(Tool(), "mail_send", {"to": "a@example.com"}, None)
    assert not d.allowed and approvals.get(d.approval_id).status == "denied"


async def test_a_later_yes_covers_only_the_action_it_was_asked_about(tmp_path):
    guard, approvals = make(tmp_path)
    ap = approvals.request("mail_send", {"to": "a@example.com", "body": "hi"}, "Send the mail to a?", risk=R3)
    approvals.decide(ap.id, True, via="telegram")
    other = guard.use_approval(ap.id, "mail_send", {"to": "b@example.com", "body": "hi"}, risk=R3)
    assert not other.allowed and "does not match" in other.reason
    same = guard.use_approval(ap.id, "mail_send", {"to": "a@example.com", "body": "hi"}, risk=R3)
    assert same.allowed and same.asked and same.approval_id == ap.id
    again = guard.use_approval(ap.id, "mail_send", {"to": "a@example.com", "body": "hi"}, risk=R3)
    assert not again.allowed  # single use


async def test_an_expired_approval_never_authorises(tmp_path):
    clock = {"now": 1000.0}
    store = SimpleNamespace(config=SimpleNamespace(safety=SafetyConfig()))
    approvals = ApprovalStore(tmp_path / "a.sqlite", clock=lambda: clock["now"], ttl_s=30)
    guard = Guard(store, approvals=approvals)
    ap = approvals.request("mail_send", {"to": "a"}, "Send?", risk=R3)
    approvals.decide(ap.id, True)
    clock["now"] += 31
    assert not guard.use_approval(ap.id, "mail_send", {"to": "a"}, risk=R3).allowed


async def test_the_inbox_can_be_switched_off(tmp_path):
    guard, approvals = make(tmp_path, approval_inbox=False)

    async def confirm(_q):
        return True

    d = await guard.authorize(Tool(), "mail_send", {"to": "a@example.com"}, confirm)
    assert d.allowed and d.approval_id is None and approvals.pending() == []


async def test_actions_that_need_no_question_leave_no_record(tmp_path):
    guard, approvals = make(tmp_path)
    read = SimpleNamespace(name="clock_get_time", risk=0, risky=False, confirm_when=None, egress=None, describe=None)
    d = await guard.authorize(read, "clock_get_time", {}, None)
    assert d.allowed and not d.asked and d.approval_id is None and approvals.pending() == []
