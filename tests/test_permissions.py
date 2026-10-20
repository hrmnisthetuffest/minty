"""Plain-language permissions and the weekly review (W1-16)."""
import time

from tests.safety_helpers import Host
from minty_core.approvals import ApprovalStore
from minty_core.commands import CommandRouter
from minty_core.config import SafetyConfig
from minty_core.permissions import describe_tool, explain_last, explain_named, review
from minty_core.receipts import ReceiptStore


def host():
    return Host({"clock_get_time": (0, None), "files_write": (1, None), "mail_send": (3, lambda a: "x"),
                 "files_delete": (3, None), "browser_click": (2, None)})


def test_each_class_has_its_own_plain_sentence():
    h = host()
    read = describe_tool(h.get_tool("clock_get_time"))
    assert "only looks things up" in read and "do not need to ask" in read
    small = describe_tool(h.get_tool("files_write"))
    assert "small changes" in small and "do not need to ask" in small
    seen = describe_tool(h.get_tool("browser_click"))
    assert "other people can see" in seen and "I ask first because your safety mode is commits" in seen
    money = describe_tool(h.get_tool("files_delete"))
    assert "I always ask first for this, whatever your settings are" in money
    assert "send information to other servers" in describe_tool(h.get_tool("mail_send"))


def test_a_tool_can_be_named_loosely_and_unknown_names_are_said_plainly():
    h = host()
    assert explain_named(h, "files delete").startswith("Files delete can spend money, use passwords or delete")
    assert explain_named(h, "teleport") == "I don't have a tool called teleport."


def test_explain_this_permission_uses_the_open_question_then_the_last_change(tmp_path):
    h = host()
    approvals = ApprovalStore(tmp_path / "a.sqlite")
    receipts = ReceiptStore(tmp_path / "r.sqlite")
    assert explain_last(h, approvals, receipts) == "I have not asked for any permission lately."
    receipts.record("files_write", 1, "write a.txt", outcome="ok")
    assert explain_last(h, approvals, receipts).startswith("Files write makes small changes")
    approvals.request("mail_send", {"to": "x"}, "Send the mail to your teacher?", risk=3)
    text = explain_last(h, approvals, receipts)
    assert text.startswith("I asked: Send the mail to your teacher?") and "Mail send" in text


def test_the_weekly_review_lists_what_was_never_used(tmp_path):
    h = host()
    receipts = ReceiptStore(tmp_path / "r.sqlite")
    receipts.record("files_write", 1, "write a.txt", outcome="ok")
    text = review(h, receipts, SafetyConfig(), now=time.time())
    assert "4 tools and used 1" in text
    assert "Never used:" in text and "mail send" in text and "files write" not in text.split("Never used:")[1]
    trusted = review(h, receipts, SafetyConfig(mode="trusted", rules=[{"match": "files_*", "decision": "allow"}]))
    assert "trusted" in trusted and "1 of your rules let something run without asking" in trusted


def test_by_voice(tmp_path):
    h = host()
    receipts = ReceiptStore(tmp_path / "r.sqlite")
    router = CommandRouter(agent=None, permissions=lambda kind: explain_last(h, None, receipts) if kind == "explain"
                           else review(h, receipts, SafetyConfig()))
    assert router.handle("explain this permission") == "I have not asked for any permission lately."
    assert "4 tools" in router.handle("permission review")
