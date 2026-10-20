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


def test_a_pin_is_four_to_eight_digits_and_stored_hashed(tmp_path):
    store = PasswordStore(tmp_path)
    store.set_pin("493017")
    assert store.verify("493017") and not store.verify("493018") and not store.verify("49301")
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600 and "493017" not in store.path.read_text()
    store.set_pin("7319")
    store.set_pin("48210563")
    for bad in ("", "123", "123456789", "12ab56", "12 34 56", "pass word", "49301\u0664"):
        with pytest.raises(ValueError):
            store.set_pin(bad)


def test_easy_to_guess_pins_are_refused(tmp_path):
    store = PasswordStore(tmp_path)
    for weak in ("0000", "1111", "123456", "654321", "1234", "9876", "012345", "55555555"):
        with pytest.raises(ValueError, match="easy to guess"):
            store.set_pin(weak)
    store.set_pin("135790")  # steps of two are not "in a row"


def test_an_old_password_still_logs_in_until_a_pin_is_set(tmp_path):
    store = PasswordStore(tmp_path)
    store.set("correct horse")
    assert store.verify("correct horse")
    store.set_pin("493017")
    assert not store.verify("correct horse") and store.verify("493017")
