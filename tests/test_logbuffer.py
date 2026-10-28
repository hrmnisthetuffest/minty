import logging

from minty_core.logbuffer import LogBuffer


def record(level, msg, name="t"):
    return logging.makeLogRecord({"name": name, "levelno": level, "levelname": logging.getLevelName(level), "msg": msg, "args": ()})


def test_keeps_last_n_and_filters_by_level():
    buf = LogBuffer(capacity=3)
    for i in range(5):
        buf.emit(record(logging.INFO, f"line {i}"))
    buf.emit(record(logging.DEBUG, "noise"))
    out = buf.recent()
    assert [r["message"] for r in out] == ["line 3", "line 4"]
    assert all(r["level"] == "INFO" for r in out)
    assert buf.recent(min_level="DEBUG")[-1]["message"] == "noise"


def test_limit_applies_to_newest():
    buf = LogBuffer()
    for i in range(10):
        buf.emit(record(logging.WARNING, f"w{i}"))
    assert [r["message"] for r in buf.recent(limit=2)] == ["w8", "w9"]


def test_secret_values_are_redacted():
    buf = LogBuffer(secret_values=lambda: ["gsk_abcdefghijklmnop", "short"])
    buf.emit(record(logging.ERROR, "call failed with key gsk_abcdefghijklmnop and short"))
    msg = buf.recent()[0]["message"]
    assert "gsk_abcdefghijklmnop" not in msg and "***" in msg
    assert "short" in msg  # values under 8 chars are not treated as secrets


def test_concurrent_emit_and_recent_do_not_raise():
    import threading

    buf = LogBuffer(capacity=500)
    errors = []

    def writer():
        for i in range(5000):
            buf.emit(record(logging.INFO, f"m{i}"))

    t = threading.Thread(target=writer)
    t.start()
    try:
        while t.is_alive():
            buf.recent()
    except Exception as e:  # pragma: no cover
        errors.append(e)
    t.join()
    assert not errors


def test_emit_never_raises_on_bad_format(monkeypatch):
    monkeypatch.setattr(logging, "raiseExceptions", False)
    buf = LogBuffer()
    buf.emit(logging.makeLogRecord({"name": "t", "levelno": logging.INFO, "levelname": "INFO", "msg": "%s %s", "args": ("x",)}))
    buf.emit(record(logging.INFO, "ok"))
    assert buf.recent()[-1]["message"] == "ok"


def test_deleted_or_rotated_secret_stays_redacted():
    current = ["gsk_abcdefghijklmnop"]
    buf = LogBuffer(secret_values=lambda: list(current))
    buf.emit(record(logging.ERROR, "key gsk_abcdefghijklmnop failed"))
    current.clear()
    msg = buf.recent()[0]["message"]
    assert "gsk_abcdefghijklmnop" not in msg and "***" in msg


def test_overlapping_secrets_replace_longest_first():
    buf = LogBuffer(secret_values=lambda: ["abcdefgh", "abcdefghXYZ12345"])
    buf.emit(record(logging.ERROR, "k=abcdefghXYZ12345"))
    assert "XYZ12345" not in buf.recent()[0]["message"]


def test_url_encoded_forms_of_secrets_are_redacted():
    from urllib.parse import quote, quote_plus

    secret = "key/with+odd chars&=1234"
    forms = {secret, quote(secret, safe=""), quote_plus(secret)}
    assert len(forms) == 3
    buf = LogBuffer(secret_values=lambda: [secret])
    for form in forms:
        buf.emit(record(logging.INFO, f"HTTP Request: GET https://api.example.com/x?apikey={form} done"))
    for row in buf.recent():
        assert not any(form in row["message"] for form in forms)
        assert row["message"].endswith("apikey=*** done")


def test_security_events_can_be_read_on_their_own():
    buf = LogBuffer()
    for name, msg in (("minty.security", "login failed from 192.168.1.9"), ("minty.core", "started"), ("minty.security.extra", "locked")):
        buf.emit(logging.LogRecord(name, logging.WARNING, __file__, 1, msg, (), None))
    got = buf.recent(min_level="INFO", name="minty.security")
    assert [r["message"] for r in got] == ["login failed from 192.168.1.9", "locked"]
    assert len(buf.recent(min_level="INFO")) == 3
