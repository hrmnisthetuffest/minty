import logging
import os
import stat

import pytest

from minty_panel.auth import LoginThrottle, PasswordStore, SessionStore, hash_password, verify_password


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


def test_hash_roundtrip_and_salting():
    salt, digest = hash_password("correct horse")
    assert verify_password("correct horse", salt, digest)
    assert not verify_password("wrong horse", salt, digest)
    assert hash_password("correct horse")[1] != digest  # fresh salt each time


def test_password_store(tmp_path):
    store = PasswordStore(tmp_path)
    assert not store.is_set() and store.verify("anything") is False
    with pytest.raises(ValueError):
        store.set("short")
    store.set("correct horse")
    assert store.is_set() and store.verify("correct horse") and not store.verify("nope")
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    assert "correct horse" not in store.path.read_text()


def test_sessions_expire_and_can_be_destroyed():
    clock = Clock()
    sessions = SessionStore(ttl=10, clock=clock)
    token = sessions.create()
    assert sessions.valid(token) and not sessions.valid("") and not sessions.valid("forged")
    clock.now += 11
    assert not sessions.valid(token)
    other = sessions.create()
    sessions.destroy(other)
    assert not sessions.valid(other)


def test_throttle_blocks_then_recovers():
    clock = Clock()
    throttle = LoginThrottle(max_failures=3, window=60, clock=clock)
    for _ in range(3):
        assert not throttle.blocked("1.2.3.4")
        throttle.record_failure("1.2.3.4")
    assert throttle.blocked("1.2.3.4") and not throttle.blocked("5.6.7.8")
    clock.now += 61
    assert not throttle.blocked("1.2.3.4")
    throttle.record_failure("1.2.3.4")
    throttle.reset("1.2.3.4")
    assert not throttle.blocked("1.2.3.4")


def test_throttle_forgets_expired_keys():
    clock = Clock()
    throttle = LoginThrottle(max_failures=1, window=60, clock=clock)
    for i in range(50):
        throttle.record_failure(f"k{i}")
    clock.now += 61
    for i in range(50):
        assert not throttle.blocked(f"k{i}")
    assert throttle._failures == {}


@pytest.mark.parametrize("content", ["not = [valid", 'salt = "00"\n', 'salt = "zz"\nhash = "00"\n'])
def test_corrupt_password_file_fails_closed(tmp_path, content, caplog):
    store = PasswordStore(tmp_path)
    store.path.write_text(content)
    with caplog.at_level(logging.WARNING):
        assert store.verify("whatever pass") is False
    assert caplog.records
