"""Guessing the panel PIN: each address that keeps failing is locked out for longer, the whole panel has a ceiling on how many PINs it
will check a minute, and every refusal leaves a line in the security log (never the PIN, never a cookie)."""
import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from minty_panel.auth import GlobalBudget, LoginThrottle, PasswordStore, SessionStore
from minty_panel.core_client import CoreClient
from minty_panel.server import create_panel_app


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


def test_every_lockout_is_longer_than_the_one_before():
    clock = Clock()
    t = LoginThrottle(max_failures=3, window=60, clock=clock)
    waits = []
    for _ in range(5):
        for _ in range(3):
            t.record_failure("a")
        assert t.blocked("a")
        waits.append(t.retry_after("a"))
        clock.now += waits[-1] + 1
        assert not t.blocked("a")
    assert waits == [60, 120, 240, 480, 960]


def test_the_lockout_is_capped():
    clock = Clock()
    t = LoginThrottle(max_failures=1, window=60, max_lock=3600, clock=clock)
    for _ in range(12):
        t.record_failure("a")
        clock.now += t.retry_after("a") + 1
    t.record_failure("a")
    assert t.retry_after("a") == 3600


def test_a_good_login_forgives_the_past_and_quiet_days_do_too():
    clock = Clock()
    t = LoginThrottle(max_failures=2, window=60, clock=clock)
    for _ in range(2):
        t.record_failure("a")
    clock.now += 61
    for _ in range(2):
        t.record_failure("a")
    assert t.retry_after("a") == 120
    t.reset("a")
    for _ in range(2):
        t.record_failure("a")
    assert t.retry_after("a") == 60
    clock.now += 61
    for _ in range(2):
        t.record_failure("a")  # a second strike
    clock.now += 25 * 3600  # a day of quiet
    for _ in range(2):
        t.record_failure("a")
    assert t.retry_after("a") == 60


def test_other_addresses_are_not_locked_by_one():
    t = LoginThrottle(max_failures=2, window=60, clock=Clock())
    t.record_failure("a")
    t.record_failure("a")
    assert t.blocked("a") and not t.blocked("b") and t.retry_after("b") == 0


def test_the_panel_checks_only_so_many_pins_a_minute_in_total():
    clock = Clock()
    budget = GlobalBudget(per_minute=3, clock=clock)
    assert [budget.take() for _ in range(5)] == [True, True, True, False, False]
    clock.now += 61
    assert budget.take() is True


HDR = {"X-Requested-With": "minty"}


def panel(tmp_path, *, budget=None, throttle=None):
    passwords = PasswordStore(tmp_path)
    passwords.set("correct horse")
    token = tmp_path / "api_token"
    token.write_text("CORE\n")
    core = CoreClient("http://core.test", token, httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))))
    app = create_panel_app(passwords, SessionStore(), throttle or LoginThrottle(), core, allowed_hosts=("testserver",),
                           login_budget=budget)
    return TestClient(app)


def post(client, pin):
    return client.post("/login", json={"password": pin}, headers=HDR)


def test_a_locked_out_address_is_told_how_long_to_wait(tmp_path):
    c = panel(tmp_path)
    for _ in range(5):
        assert post(c, "wrong pin!").status_code == 401
    res = post(c, "correct horse")
    assert res.status_code == 429 and res.headers["retry-after"] == "60" and "60 seconds" in res.json()["detail"]


def test_the_whole_panel_slows_down_when_many_addresses_guess(tmp_path):
    c = panel(tmp_path, budget=GlobalBudget(per_minute=2))
    assert post(c, "wrong pin!").status_code == 401
    assert post(c, "wrong pin!").status_code == 401
    res = post(c, "correct horse")
    assert res.status_code == 429 and "busy" in res.json()["detail"].lower()


def lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "minty.security"]


def test_refused_logins_are_logged_without_the_pin(tmp_path, caplog):
    c = panel(tmp_path)
    with caplog.at_level(logging.INFO, logger="minty.security"):
        post(c, "hunter2-wrong")
        assert post(c, "correct horse").status_code == 204
        c.post("/login", json={"password": "x"})  # no CSRF header
    text = "\n".join(lines(caplog))
    assert "login failed" in text and "login ok" in text and "missing csrf header" in text
    assert "hunter2" not in text and "correct horse" not in text and "minty_session" not in text


def test_lockouts_and_foreign_hosts_and_bad_sockets_are_logged(tmp_path, caplog):
    c = panel(tmp_path)
    with caplog.at_level(logging.INFO, logger="minty.security"):
        for _ in range(6):
            post(c, "wrong pin!")
        c.get("/", headers={"Host": "evil.example"})
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/api/events") as ws:
                ws.receive_text()
    text = "\n".join(lines(caplog))
    assert "locked" in text and "bad host" in text and "events socket refused" in text
    assert "evil.example" in text  # the host is useful to the owner and is not a secret
