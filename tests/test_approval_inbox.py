"""The approval inbox: ask what is waiting, approve or deny it by voice or the API (W1-03)."""
import pytest
from fastapi.testclient import TestClient

from tests.fakes import FakeAgent
from minty_core.api import create_app
from minty_core.approvals import ApprovalStore
from minty_core.commands import CommandRouter
from minty_core.context import build_context


@pytest.fixture
def approvals(tmp_path):
    return ApprovalStore(tmp_path / "approvals.sqlite")


def router(approvals):
    return CommandRouter(agent=None, approvals=approvals)


def test_nothing_waiting(approvals):
    assert router(approvals).handle("what needs my approval") == "Nothing is waiting for your approval."
    assert router(approvals).handle("approve it") == "Nothing is waiting for your approval."


def test_the_list_is_short_and_numbered(approvals):
    a = approvals.request("mail_send", {"to": "x"}, "Send the mail to your teacher?", risk=3)
    out = router(approvals).handle("what is waiting for my approval")
    assert f"number {a.id}" in out.lower() and "Send the mail to your teacher?" in out
    b = approvals.request("files_delete", {"p": "x"}, "Delete notes.txt?", risk=3)
    out = router(approvals).handle("any approvals pending")
    assert "Two things" in out and f"number {a.id}" in out.lower() and f"number {b.id}" in out.lower()


def test_approve_it_decides_the_only_pending_one(approvals):
    a = approvals.request("mail_send", {"to": "x"}, "Send the mail?", risk=3)
    assert router(approvals).handle("approve it") == "Approved."
    assert approvals.get(a.id).status == "approved"
    assert router(approvals).handle("approve it") == "Nothing is waiting for your approval."


def test_deny_it(approvals):
    a = approvals.request("mail_send", {"to": "x"}, "Send the mail?", risk=3)
    assert router(approvals).handle("deny it") == "Denied."
    assert approvals.get(a.id).status == "denied"


def test_with_several_waiting_it_never_guesses(approvals):
    a = approvals.request("a", {}, "Do a?")
    b = approvals.request("b", {}, "Do b?")
    out = router(approvals).handle("approve it")
    assert "which one" in out.lower() and approvals.get(a.id).status == "pending"
    assert router(approvals).handle(f"approve number {b.id}") == "Approved."
    assert approvals.get(b.id).status == "approved" and approvals.get(a.id).status == "pending"


def test_a_stale_number_is_said_plainly(approvals):
    assert router(approvals).handle("approve number 77") == "I can't find that one, or it was already decided."


def test_other_phrases_are_not_swallowed(approvals):
    approvals.request("a", {}, "Do a?")
    assert router(approvals).handle("approve of this idea") is None
    assert router(approvals).handle("what time is it") is None


@pytest.fixture
def ctx(tmp_path):
    c = build_context(tmp_path)
    c.agent = FakeAgent([["Hello there."]])
    return c


def test_the_api_lists_and_decides_with_the_token(ctx):
    client = TestClient(create_app(ctx))
    auth = {"Authorization": f"Bearer {ctx.token}"}
    ap = ctx.services["approvals"].request("mail_send", {"to": "x"}, "Send the mail?", risk=3)
    assert client.get("/approvals").status_code == 401
    listed = client.get("/approvals", headers=auth).json()
    assert [r["id"] for r in listed] == [ap.id] and listed[0]["question"] == "Send the mail?"
    assert "args" not in listed[0] and listed[0]["expires_in"] > 0
    assert client.post(f"/approvals/{ap.id}/approve", headers=auth).json() == {"ok": True}
    assert client.post(f"/approvals/{ap.id}/deny", headers=auth).json() == {"ok": False}
    assert ctx.services["approvals"].get(ap.id).status == "approved"


def test_a_chat_message_cannot_approve_anything(ctx):
    client = TestClient(create_app(ctx))
    auth = {"Authorization": f"Bearer {ctx.token}"}
    ap = ctx.services["approvals"].request("mail_send", {"to": "x"}, "Send the mail?", risk=3)
    reply = client.post("/ask", json={"text": "approve it"}, headers=auth).json()["reply"]
    assert reply == "Hello there."  # went to the assistant, not to the inbox
    assert ctx.services["approvals"].get(ap.id).status == "pending"
