"""Approvals are bound to the exact action they approved, expire, and fail closed (W1-01)."""
import sqlite3

from minty_core.approvals import ApprovalStore, action_hash


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def store(tmp_path, clock=None, ttl=300.0):
    return ApprovalStore(tmp_path / "approvals.sqlite", clock or Clock(), ttl_s=ttl)


def test_the_hash_ignores_argument_order_and_spacing_but_not_content():
    a = action_hash("mail_send", {"to": "a@example.com", "body": "hello  there", "subject": "hi"})
    b = action_hash("mail_send", {"subject": "hi", "body": "hello there", "to": "a@example.com"})
    assert a == b
    assert a != action_hash("mail_send", {"to": "b@example.com", "body": "hello there", "subject": "hi"})
    assert a != action_hash("mail_delete", {"to": "a@example.com", "body": "hello there", "subject": "hi"})
    assert action_hash("t", {"n": 1}) == action_hash("t", {"n": 1.0})
    assert action_hash("t", {"x": 1}, target="a.example") != action_hash("t", {"x": 1}, target="b.example")


def test_an_approved_action_is_consumed_once_by_the_same_action_only(tmp_path):
    s = store(tmp_path)
    ap = s.request("browser_click", {"ref": "e5"}, "Click Buy now?", risk=3)
    assert ap.status == "pending" and s.consume(ap.id, "browser_click", {"ref": "e5"}) is False  # not decided yet
    assert s.decide(ap.id, True, via="voice") is True
    assert s.consume(ap.id, "browser_click", {"ref": "e9"}) is False  # a different action: no
    assert s.get(ap.id).status == "approved"  # a wrong attempt does not use it up
    assert s.consume(ap.id, "browser_click", {"ref": "e5"}) is True
    assert s.consume(ap.id, "browser_click", {"ref": "e5"}) is False  # single use
    assert s.get(ap.id).status == "used"


def test_denied_unknown_and_expired_approvals_refuse(tmp_path):
    clock = Clock()
    s = store(tmp_path, clock, ttl=60)
    denied = s.request("files_delete", {"path": "a"}, "Delete a?")
    s.decide(denied.id, False)
    assert s.consume(denied.id, "files_delete", {"path": "a"}) is False
    assert s.consume(9999, "files_delete", {"path": "a"}) is False
    late = s.request("files_delete", {"path": "b"}, "Delete b?")
    s.decide(late.id, True)
    clock.now += 61
    assert s.consume(late.id, "files_delete", {"path": "b"}) is False
    assert s.get(late.id).status == "expired"
    stale = s.request("files_delete", {"path": "c"}, "Delete c?")
    clock.now += 61
    assert s.decide(stale.id, True) is False  # too late to approve


def test_deciding_twice_changes_nothing(tmp_path):
    s = store(tmp_path)
    ap = s.request("t", {}, "Do t?")
    assert s.decide(ap.id, True) is True
    assert s.decide(ap.id, False) is False
    assert s.get(ap.id).status == "approved"


def test_pending_survives_a_restart_and_arguments_are_not_kept(tmp_path):
    s = store(tmp_path)
    ap = s.request("mail_send", {"to": "a@example.com", "body": "my password is hunter2"}, "Send the mail?", risk=2)
    reopened = store(tmp_path)
    assert [p.id for p in reopened.pending()] == [ap.id]
    raw = sqlite3.connect(tmp_path / "approvals.sqlite").execute("select * from approvals").fetchall()
    assert "hunter2" not in str(raw) and "a@example.com" not in str(raw)  # only the hash, the tool and the question


def test_pending_lists_newest_last_and_leaves_out_what_expired(tmp_path):
    clock = Clock()
    s = store(tmp_path, clock, ttl=100)
    first = s.request("a", {}, "A?")
    clock.now += 60
    second = s.request("b", {}, "B?")
    clock.now += 50  # the first is now 110 s old
    assert [p.id for p in s.pending()] == [second.id]
    assert s.get(first.id).status == "expired"


def test_the_database_uses_write_ahead_logging(tmp_path):
    store(tmp_path)
    mode = sqlite3.connect(tmp_path / "approvals.sqlite").execute("pragma journal_mode").fetchone()[0]
    assert mode == "wal"


def test_old_decided_records_are_dropped_when_it_opens(tmp_path):
    clock = Clock()
    s = store(tmp_path, clock)
    old = s.request("a", {}, "Old?")
    s.decide(old.id, True)
    kept = s.request("b", {}, "Waiting?")  # still pending: never dropped
    clock.now += 31 * 86400
    reopened = ApprovalStore(tmp_path / "approvals.sqlite", clock)
    assert reopened.get(old.id) is None
    assert reopened.get(kept.id).status == "expired"  # a question nobody answered is expired, and then dropped later
