"""Every change leaves a receipt, and "why did you do that" is answered from it (W1-15)."""
from datetime import datetime

from tests.safety_helpers import build, call, say
from minty_core.commands import CommandRouter
from minty_core.receipts import ReceiptStore, today_reply, why_reply
from minty_core.safety import SafetyKit
from minty_core.secretscan import SecretScanner


def test_store_round_trip_pruning_and_redaction(tmp_path):
    clock = {"now": 1_000_000.0}
    s = ReceiptStore(tmp_path / "r.sqlite", lambda: clock["now"], redact=SecretScanner().redact)
    rid = s.record("files_write", 1, "write notes.txt with gsk_" + "ab12" * 8, request="save a note", outcome="ok")
    got = s.recent(1)[0]
    assert got.id == rid and "gsk_" not in got.summary and got.request == "save a note"
    clock["now"] += 91 * 86400
    assert ReceiptStore(tmp_path / "r.sqlite", lambda: clock["now"]).recent() == []


def test_the_answer_names_the_request_the_approval_and_the_result(tmp_path):
    s = ReceiptStore(tmp_path / "r.sqlite")
    assert "nothing to explain" in why_reply(s)
    s.record("mail_send", 3, "send the mail to your teacher", request="mail my teacher I will be late",
             approval_id=7, outcome="ok")
    text = why_reply(s)
    assert "send the mail to your teacher" in text and "mail my teacher I will be late" in text
    assert "said yes" in text and text.endswith("It worked.")
    s.record("files_delete", 3, "delete notes.txt", request="clean up", outcome="declined")
    assert why_reply(s).startswith("I did not delete notes.txt: you said no.")
    s.record("files_write", 1, "write a.txt", outcome="blocked", detail="lockdown is on")
    assert "a safety check stopped it (lockdown is on)" in why_reply(s)


def test_today_lists_what_changed(tmp_path):
    s = ReceiptStore(tmp_path / "r.sqlite")
    assert today_reply(s) == "I haven't changed anything today."
    s.record("files_write", 1, "write notes.txt", outcome="ok")
    s.record("files_write", 1, "write b.txt", outcome="failed", detail="disk full")
    s.record("files_delete", 3, "delete c.txt", outcome="declined")
    out = today_reply(s)
    assert out.startswith("2 changes today.") and "write notes.txt" in out and "delete c.txt" not in out


def kit(store):
    return SafetyKit(store, SecretScanner())


async def test_the_agent_leaves_a_receipt_for_changes_declines_and_blocks_but_not_reads(tmp_path):
    s = ReceiptStore(tmp_path / "r.sqlite")
    agent, host, asked = build(
        tmp_path, {"clock_get_time": (0, None), "files_write": (1, None), "mail_send": (3, None)},
        [[call("clock_get_time", ident="1")], [call("files_write", {"path": "n.txt"}, ident="2")],
         [call("mail_send", {"to": "t@school.example"}, ident="3")]], confirm_answer=False, safety=kit)
    agent.receipts = s
    agent._max_rounds = 5
    await say(agent, "note it and mail the teacher")
    rows = s.recent(10)
    assert [r.tool for r in reversed(rows)] == ["files_write", "mail_send"]  # the clock left none
    assert rows[0].outcome == "declined" and rows[0].request == "note it and mail the teacher"  # the mail: asked, no
    assert rows[1].outcome == "ok" and rows[1].approval_id is None  # the small change needed no question


def test_by_voice(tmp_path):
    s = ReceiptStore(tmp_path / "r.sqlite")
    s.record("files_write", 1, "write notes.txt", request="save a note", outcome="ok")
    router = CommandRouter(agent=None, receipts=s)
    assert "write notes.txt" in router.handle("why did you do that")
    assert "write notes.txt" in router.handle("what did you just do")
    assert "1 change today" in router.handle("what have you changed today")
