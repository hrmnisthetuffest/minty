"""Gmail read through Composio: the real response shapes, reads only, and nothing without the pinned, allowed tool."""
import json
import socket

import httpx
import pytest

from minty_core.composio_gmail import ComposioGmail, ComposioRunner
from minty_core.gmailwatch import HistoryExpired
from minty_core.mcp.registry import McpRegistry, tool_hash
from tests.plugin_helpers import mock_http
from tests.test_mcp_remote import EXECUTE, PUBLIC, Server

SEARCH = {"name": "COMPOSIO_SEARCH_TOOLS", "description": "d", "inputSchema": {"type": "object"}}


def wrap(data, ok=True, error=None):
    return {"successful": True, "data": {"results": [{"response": {"successful": ok, "data": data, "error": error}, "tool_slug": "X", "index": 0}],
                                        "total_count": 1, "success_count": int(ok), "error_count": int(not ok)}, "error": None, "log_id": "l"}


class FakeRunner:
    """Answers by tool slug; records every call."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def round(self):
        import contextlib
        return contextlib.nullcontext()

    async def call(self, tool, arguments, max_chars=0):
        self.calls.append((tool, arguments))
        slug = arguments["tools"][0]["tool_slug"] if tool == "COMPOSIO_MULTI_EXECUTE_TOOL" else tool
        answer = self.answers[slug]
        if callable(answer):
            answer = answer(arguments)
        if isinstance(answer, Exception):
            raise answer
        return answer


async def test_the_connected_accounts_come_from_composios_own_list():
    status = {"data": {"toolkit_connection_statuses": [{"toolkit": "gmail", "accounts": [
        {"id": "gmail_a", "alias": "work", "status": "ACTIVE", "user_info": {"email": "w@x.test"}},
        {"id": "gmail_b", "alias": "", "status": "ACTIVE", "user_info": {"email": "p@x.test"}},
        {"id": "gmail_c", "alias": "old", "status": "EXPIRED", "user_info": {"email": "o@x.test"}}]}]}, "successful": True}
    g = ComposioGmail(FakeRunner({"COMPOSIO_SEARCH_TOOLS": status}))
    assert await g.accounts() == ["work", "gmail_b"] and g.emails == {"work": "w@x.test", "gmail_b": "p@x.test"}


async def test_the_baseline_is_the_history_position_of_the_profile():
    runner = FakeRunner({"GMAIL_GET_PROFILE": wrap({"emailAddress": "w@x.test", "historyId": "4242", "messagesTotal": 10})})
    assert await ComposioGmail(runner).baseline("work") == "4242"
    step = runner.calls[0][1]["tools"][0]
    assert step["account"] == "work" and runner.calls[0][1]["sync_response_to_workbench"] is False


async def test_new_messages_come_from_the_history_with_their_labels_and_the_new_position():
    history = wrap({"history": [{"id": "1", "messages": [{"id": "a", "threadId": "ta"}],
                                 "messagesAdded": [{"message": {"id": "a", "threadId": "ta", "labelIds": ["INBOX", "UNREAD"]}}]},
                                {"id": "2", "messagesAdded": [{"message": {"id": "b", "threadId": "tb", "labelIds": ["SENT"]}}]}], "historyId": "4300"})
    runner = FakeRunner({"GMAIL_LIST_HISTORY": history})
    items, new = await ComposioGmail(runner).new_messages("work", "4242")
    assert items == [{"id": "a", "thread": "ta", "labels": ["INBOX", "UNREAD"]}, {"id": "b", "thread": "tb", "labels": ["SENT"]}] and new == "4300"
    args = runner.calls[0][1]["tools"][0]["arguments"]
    assert args["start_history_id"] == "4242" and args["history_types"] == ["messageAdded"]


async def test_an_empty_history_is_nothing_new_and_keeps_the_position():
    g = ComposioGmail(FakeRunner({"GMAIL_LIST_HISTORY": wrap({"history": [], "historyId": "4300"})}))
    assert await g.new_messages("work", "4242") == ([], "4300")


async def test_a_history_position_gmail_no_longer_has_is_reported_as_expired():
    g = ComposioGmail(FakeRunner({"GMAIL_LIST_HISTORY": wrap({}, ok=False, error="HTTP 404: Requested entity was not found. startHistoryId is too old")}))
    with pytest.raises(HistoryExpired):
        await g.new_messages("work", "1")


async def test_other_failures_are_errors_not_silence():
    g = ComposioGmail(FakeRunner({"GMAIL_GET_PROFILE": wrap({}, ok=False, error="HTTP 500")}))
    with pytest.raises(RuntimeError):
        await g.baseline("work")


async def test_a_messages_metadata_becomes_plain_bounded_fields():
    data = {"labelIds": ["INBOX", "UNREAD"], "messageId": "a", "threadId": "ta", "sender": "Alex <alex@acme.test>", "to": "Me <me@x.test>, B <b@x.test>",
            "subject": "Invoice " + "x" * 400, "preview": {"body": "Please pay " + "y" * 400, "subject": "s"}, "messageTimestamp": "2026-10-28T17:45:12Z",
            "payload": {"headers": [{"name": "List-Unsubscribe", "value": "<mailto:u@x.test>"}, {"name": "Received", "value": "junk"},
                                    {"name": "Authentication-Results", "value": "mx; spf=pass"}]}}
    mail = await ComposioGmail(FakeRunner({"GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID": wrap(data)})).metadata("work", "a")
    assert mail.id == "a" and mail.thread_id == "ta" and mail.sender == "Alex <alex@acme.test>" and mail.to == ("me@x.test", "b@x.test")
    assert len(mail.subject) <= 200 and len(mail.snippet) <= 300 and mail.labels == ("INBOX", "UNREAD") and mail.date_ts > 1_700_000_000
    assert set(mail.headers) == {"list-unsubscribe", "authentication-results"}  # only the headers the scorer reads


async def test_recent_mail_is_asked_for_by_time_and_a_sender_lookup_by_address_only():
    runner = FakeRunner({"GMAIL_FETCH_EMAILS": lambda a: wrap({"messages": [{"messageId": "z", "threadId": "tz"}]})})
    g = ComposioGmail(runner, clock=lambda: 1_800_000_000.0)
    assert await g.recent_messages("work", 180) == [{"id": "z", "thread": "tz", "labels": ["INBOX"]}]
    assert "after:1799989200" in runner.calls[0][1]["tools"][0]["arguments"]["query"]
    assert await g.sender_known("work", "alex@acme.test") is True
    with pytest.raises(ValueError):
        await g.sender_known("work", "x@y.test in:anywhere OR from:z")  # a query is never built from message text


async def test_nothing_but_reads_is_ever_asked_of_composio():
    runner = FakeRunner({})
    g = ComposioGmail(runner)
    with pytest.raises(PermissionError):
        await g._execute("work", "GMAIL_SEND_EMAIL", {})
    assert runner.calls == []


# ---- the runner: the pinned, allowed tool only ------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def dns(monkeypatch):
    table = {"connect.composio.dev": [PUBLIC]}
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, *a, **kw: [(socket.AF_INET, 0, 0, "", (table[host][0], port))])


class Canned(Server):
    def __call__(self, request):
        body = json.loads(request.content) if request.content else None
        if body and body.get("method") == "tools/call":
            return self.reply(body["id"], {"content": [{"type": "text", "text": json.dumps(wrap({"emailAddress": "w@x.test", "historyId": "9"}))}]})
        return super().__call__(request)


def runner_for(tmp_path, monkeypatch, allowed=True, changed=False):
    reg = McpRegistry(tmp_path / "mcp.json")
    reg.add_remote("composio", "https://connect.composio.dev/mcp", {"x-consumer-api-key": "COMPOSIO_API_KEY"})
    reg.record_tools("composio", [EXECUTE])
    if allowed:
        reg.allow("composio", "COMPOSIO_MULTI_EXECUTE_TOOL", tool_hash(dict(EXECUTE, description="other") if changed else EXECUTE), readonly=False)
    mock_http(monkeypatch, Canned(tools=(EXECUTE,)))
    return ComposioRunner(reg, lambda name: "KEY", "composio", timeout=10)


async def test_the_runner_calls_composio_when_the_tool_is_allowed_and_unchanged(tmp_path, monkeypatch):
    runner = runner_for(tmp_path, monkeypatch)
    async with runner.round():
        got = await runner.call("COMPOSIO_MULTI_EXECUTE_TOOL", {"tools": [{"tool_slug": "GMAIL_GET_PROFILE", "arguments": {}}], "sync_response_to_workbench": False})
    assert got["data"]["results"][0]["response"]["data"]["historyId"] == "9"


async def test_the_runner_refuses_when_you_have_not_allowed_the_tool_or_it_changed(tmp_path, monkeypatch):
    for i, kw in enumerate(({"allowed": False}, {"changed": True})):
        (tmp_path / str(i)).mkdir()
        runner = runner_for(tmp_path / str(i), monkeypatch, **kw)
        with pytest.raises(PermissionError):
            async with runner.round():
                pass
