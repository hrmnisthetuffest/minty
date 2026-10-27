import asyncio
import email
from types import SimpleNamespace

import pytest

from tests.fake_smtp import FakeSMTP
from tests.plugin_helpers import make_host

THREADS = {
    "7": {"uid": "7", "from_addr": "mum@home.in", "from_name": "Mum", "subject": "dinner?", "message_id": "<abc@home.in>",
          "references": "", "authenticated": True, "reply_to_differs": False},
    "8": {"uid": "8", "from_addr": "stranger@x.com", "from_name": "Someone", "subject": "hello", "message_id": "<s1@x.com>",
          "references": "", "authenticated": True, "reply_to_differs": False},
    "9": {"uid": "9", "from_addr": "mum@home.in", "from_name": "Mum", "subject": "dinner?", "message_id": "<spoof@x>",
          "references": "", "authenticated": None, "reply_to_differs": False},
    "10": {"uid": "10", "from_addr": "mum@home.in", "from_name": "Mum", "subject": "photos", "message_id": "<p@home.in>",
           "references": "<old@home.in>", "authenticated": True, "reply_to_differs": True},
}


def build(tmp_path, port, **settings):
    added = []

    async def thread(uid):
        return THREADS.get(str(uid))

    butler = SimpleNamespace(add=lambda domain, name, when, data, source="agent": added.append((domain, name, when)))
    services = {"mail_thread": thread, "butler": SimpleNamespace(butler=lambda: butler)}
    cfg = {"enabled": True, "user": "me@example.com", "smtp_host": "127.0.0.1", "smtp_port": port, "smtp_security": "none",
           "auto_reply_to": "mum@home.in, @school.edu", "auto_max_per_day": 2, **settings}
    host = make_host(tmp_path, plugins={"outbox": cfg}, secrets={"plugin.outbox.password": "pw"}, services=services)
    return host, added


async def gated(host, ident):
    """Send the way the app does: the safety gate looks at the call (and works out its risk) before the tool runs."""
    host.get_tool("outbox_send").risk({"id": ident})
    return await host.call("outbox_send", {"id": ident})


def parsed(message: dict):
    return email.message_from_string(message["data"])


def test_sending_mail_is_off_until_it_is_switched_on(tmp_path):
    assert make_host(tmp_path).get_tool("outbox_send") is None


def test_no_tool_can_change_the_policy(tmp_path):
    host, _ = build(tmp_path, 1)
    # the policy lives in settings only the user edits; the model gets exactly these four tools and no way to touch it
    assert {s.name for s in host.tool_specs() if s.name.startswith("outbox_")} == {
        "outbox_draft", "outbox_send", "outbox_list", "outbox_discard"}


async def test_a_reply_goes_to_the_person_who_wrote_and_nowhere_else(tmp_path):
    async with FakeSMTP() as smtp:
        host, _ = build(tmp_path, smtp.port)
        out = await host.call("outbox_draft", {"reply_to": "7", "body": "Sounds good, see you at 7."})
        assert "Draft 1" in out and "mum@home.in" in out and "without asking" in out and "Nothing is sent" in out
        redirect = await host.call("outbox_draft", {"reply_to": "7", "to": "evil@x.com", "body": "hi"})
        assert redirect.startswith("Error") and "recipient" in redirect and smtp.messages == []


async def test_an_allowed_authenticated_reply_is_sent_automatically_and_threaded(tmp_path):
    async with FakeSMTP() as smtp:
        host, _ = build(tmp_path, smtp.port)
        await host.call("outbox_draft", {"reply_to": "7", "body": "Sounds good, see you at 7."})
        tool = host.get_tool("outbox_send")
        assert tool.risk({"id": 1}) == 1  # the user's own rule made this routine
        out = await gated(host, 1)
        assert "Sent to mum@home.in" in out and "automatically" in out
        assert len(smtp.messages) == 1
        msg = parsed(smtp.messages[0])
        assert msg["To"] == "mum@home.in" and msg["From"] == "me@example.com" and msg["Subject"] == "Re: dinner?"
        assert msg["In-Reply-To"] == "<abc@home.in>" and msg["Message-ID"]
        assert "Sounds good" in msg.get_payload() and "Minty" in msg.get_payload()  # says it was sent by the assistant
        assert smtp.logins == [("me@example.com", "pw")]
        assert "sent" in await host.call("outbox_list", {})


async def test_sending_twice_never_sends_twice(tmp_path):
    async with FakeSMTP() as smtp:
        host, _ = build(tmp_path, smtp.port)
        await host.call("outbox_draft", {"reply_to": "7", "body": "ok"})
        await gated(host, 1)
        again = await gated(host, 1)
        assert "already sent" in again and len(smtp.messages) == 1


async def test_two_sends_at_the_same_moment_send_one_message(tmp_path):
    async with FakeSMTP() as smtp:
        host, _ = build(tmp_path, smtp.port)
        await host.call("outbox_draft", {"reply_to": "7", "body": "ok"})
        host.get_tool("outbox_send").risk({"id": 1})
        await asyncio.gather(host.call("outbox_send", {"id": 1}), host.call("outbox_send", {"id": 1}))
        assert len(smtp.messages) == 1


@pytest.mark.parametrize("uid,why", [("8", "not on your auto-reply list"), ("9", "could not confirm"),
                                     ("10", "could not confirm")])
async def test_anything_outside_the_rules_is_a_question_showing_the_whole_message(tmp_path, uid, why):
    async with FakeSMTP() as smtp:
        host, _ = build(tmp_path, smtp.port)
        out = await host.call("outbox_draft", {"reply_to": uid, "body": "Thanks, I will think about it."})
        assert "ask you first" in out and why in out
        tool = host.get_tool("outbox_send")
        assert tool.risk({"id": 1}) == 3  # always asked, whatever the safety mode
        prompt = tool.describe({"id": 1})
        assert "Thanks, I will think about it." in prompt and why in prompt and THREADS[uid]["from_addr"] in prompt
        assert smtp.messages == []


async def test_a_new_conversation_is_a_question_and_goes_after_a_yes(tmp_path):
    async with FakeSMTP() as smtp:
        host, _ = build(tmp_path, smtp.port)
        out = await host.call("outbox_draft", {"to": "teacher@school.edu", "subject": "Absence on Friday",
                                                "body": "Hello, my son will be absent on Friday."})
        assert "ask you first" in out and "new conversation" in out
        assert host.get_tool("outbox_send").risk({"id": 1}) == 3
        sent = await gated(host, 1)  # the gate has asked and been told yes by now
        assert "Sent to teacher@school.edu" in sent and "after your yes" in sent
        assert parsed(smtp.messages[0])["Subject"] == "Absence on Friday"
        assert "Minty" not in parsed(smtp.messages[0]).get_payload()  # you approved the words; no automatic-sender note


async def test_the_daily_cap_turns_automatic_into_asking(tmp_path):
    async with FakeSMTP() as smtp:
        host, _ = build(tmp_path, smtp.port)
        for _ in range(2):
            await host.call("outbox_draft", {"reply_to": "7", "body": "ok"})
        await gated(host, 1)
        await gated(host, 2)
        await host.call("outbox_draft", {"reply_to": "7", "body": "one more"})
        assert host.get_tool("outbox_send").risk({"id": 3}) == 3
        assert "2 a day" in host.get_tool("outbox_send").describe({"id": 3})


async def test_a_refused_draft_is_not_even_stored(tmp_path):
    host, _ = build(tmp_path, 1)
    for args in ({"to": "a@b.com\nBcc: x@y.com", "subject": "s", "body": "b"},
                 {"to": ",".join(f"p{i}@x.com" for i in range(9)), "subject": "s", "body": "b"},
                 {"to": "a@b.com", "subject": "s", "body": "  "}, {"to": "nonsense", "subject": "s", "body": "b"}):
        assert (await host.call("outbox_draft", args)).startswith("Error")
    assert "empty" in (await host.call("outbox_list", {})).lower()


async def test_a_failed_login_says_nothing_was_sent_and_can_be_retried(tmp_path):
    async with FakeSMTP(reject_auth=True) as smtp:
        host, _ = build(tmp_path, smtp.port)
        await host.call("outbox_draft", {"reply_to": "7", "body": "ok"})
        out = await gated(host, 1)
        assert out.startswith("Error") and "was not sent" in out and smtp.messages == []
        assert "failed" in await host.call("outbox_list", {})
        smtp.reject_auth = False
        assert "Sent to" in await gated(host, 1)


async def test_a_connection_lost_mid_send_is_reported_as_unknown_and_never_resent(tmp_path):
    async with FakeSMTP(drop_after_data=True) as smtp:
        host, _ = build(tmp_path, smtp.port)
        await host.call("outbox_draft", {"reply_to": "7", "body": "ok"})
        out = await gated(host, 1)
        assert "may or may not" in out and "Sent folder" in out
        assert "unknown" in await host.call("outbox_list", {})
        smtp.drop_after_data = False
        again = await gated(host, 1)
        assert "might already" in again and smtp.messages == []
        assert "Discarded" in await host.call("outbox_discard", {"id": 1})


async def test_a_crash_in_the_middle_of_a_send_shows_as_unknown_after_the_restart(tmp_path):
    async with FakeSMTP() as smtp:
        host, _ = build(tmp_path, smtp.port)
        await host.call("outbox_draft", {"reply_to": "7", "body": "ok"})
        import sqlite3
        db = sqlite3.connect(tmp_path / "outbox.sqlite")
        db.execute("UPDATE outbox SET status='sending' WHERE id=1")
        db.commit()
        db.close()
        host2, _ = build(tmp_path, smtp.port)
        assert "unknown" in await host2.call("outbox_list", {})
        assert smtp.messages == []


async def test_a_follow_up_is_recorded_as_something_you_are_waiting_for(tmp_path):
    async with FakeSMTP() as smtp:
        host, added = build(tmp_path, smtp.port)
        await host.call("outbox_draft", {"reply_to": "7", "body": "Can you send the form?", "follow_up_days": 3})
        out = await gated(host, 1)
        assert "follow up" in out.lower()
        assert added and added[0][0] == "waiting" and "mum@home.in" in added[0][1] and "3 days" in added[0][2]


async def test_sending_is_blocked_until_the_account_is_set_up(tmp_path):
    host = make_host(tmp_path, plugins={"outbox": {"enabled": True}}, services={})
    tool = host.get_tool("outbox_send")
    assert "outbox.user" in tool.blocked_when({"id": 1}) and "plugin.outbox.password" in tool.blocked_when({"id": 1})


async def test_the_message_is_checked_for_secrets_before_it_can_go_automatically(tmp_path):
    async with FakeSMTP() as smtp:
        host, _ = build(tmp_path, smtp.port)
        out = await host.call("outbox_draft", {"reply_to": "7", "body": "my key is sk-abcdefghijklmnopqrstuvwxyz123456"})
        assert "ask you first" in out and "secret" in out


async def test_discard_and_unknown_ids(tmp_path):
    host, _ = build(tmp_path, 1)
    await host.call("outbox_draft", {"reply_to": "7", "body": "ok"})
    assert "Discarded" in await host.call("outbox_discard", {"id": 1})
    assert "discarded" in (await gated(host, 1)).lower()
    assert "No outbox item 99" in await host.call("outbox_send", {"id": 99})


async def test_a_send_that_never_went_through_the_safety_check_is_refused(tmp_path):
    async with FakeSMTP() as smtp:
        host, _ = build(tmp_path, smtp.port)
        await host.call("outbox_draft", {"reply_to": "8", "body": "hello"})  # a stranger: this must be asked
        out = await host.call("outbox_send", {"id": 1})  # as if some other code called the tool without the gate
        assert "safety check" in out and smtp.messages == []
        assert "drafted" in await host.call("outbox_list", {})
        assert "Sent to stranger@x.com" in await gated(host, 1)  # asked and answered: this one goes
        await host.call("outbox_draft", {"reply_to": "8", "body": "and another"})
        assert "safety check" in await host.call("outbox_send", {"id": 2})  # the first check does not cover the next
        assert len(smtp.messages) == 1
