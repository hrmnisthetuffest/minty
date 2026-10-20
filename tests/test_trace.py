import json
import threading
import time

import pytest

from minty_core.trace import NullTracer, Tracer, read_events


class Clock:
    def __init__(self, now=None):
        self.now = now or time.mktime((2026, 10, 10, 12, 0, 0, 0, 0, -1))

    def __call__(self):
        return self.now


def lines(directory):
    return [json.loads(l) for p in sorted(directory.glob("*.jsonl")) for l in p.read_text().splitlines()]


def test_events_are_json_lines_with_time_and_kind(tmp_path):
    t = Tracer(tmp_path, clock=Clock())
    t.event("tool_call", name="clock_now", ok=True, ms=1.5)
    t.close()
    [record] = lines(tmp_path)
    assert record["kind"] == "tool_call" and record["name"] == "clock_now" and record["ok"] is True
    assert isinstance(record["ts"], float)
    assert (tmp_path / "2026-10-10.jsonl").stat().st_mode & 0o777 == 0o600


def test_credential_fields_and_secret_values_never_reach_disk(tmp_path):
    secret = "gsk_live_ABCDEFGHIJ1234567890"
    t = Tracer(tmp_path, secret_values=lambda: [secret, "short"], clock=Clock())
    t.event("tool_call", args={"api_key": "x" * 20, "query": f"use {secret} now", "nested": {"token": "abc"}},
            note=[f"..{secret}.."], empty_key="")
    t.close()
    text = (tmp_path / "2026-10-10.jsonl").read_text()
    assert secret not in text and "xxxxxxxx" not in text
    [record] = lines(tmp_path)
    assert record["args"]["api_key"] == "…" and record["args"]["nested"]["token"] == "…"
    assert record["args"]["query"] == "use [secret] now" and record["note"] == ["..[secret].."]


def test_long_strings_and_lists_are_cut(tmp_path):
    t = Tracer(tmp_path, clock=Clock())
    t.event("x", text="a" * 1000, items=list(range(50)), obj=object())
    t.close()
    [record] = lines(tmp_path)
    assert len(record["text"]) == 301 and record["text"].endswith("…")
    assert len(record["items"]) == 21 and record["items"][-1] == "(+30 more)"
    assert isinstance(record["obj"], str)


def test_concurrent_writers_never_interleave(tmp_path):
    t = Tracer(tmp_path, clock=Clock())

    def work(n):
        for i in range(20):
            t.event("e", thread=n, i=i, pad="y" * 200)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(50)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    t.close()
    assert len(lines(tmp_path)) == 1000


def test_span_times_the_block_and_records_errors(tmp_path):
    t = Tracer(tmp_path, clock=Clock())
    with t.span("llm_call", provider="groq") as data:
        data["outcome"] = "ok"
    with pytest.raises(ValueError):
        with t.span("tool_call", name="boom"):
            raise ValueError("nope")
    t.close()
    ok, bad = lines(tmp_path)
    assert ok["outcome"] == "ok" and ok["ms"] >= 0
    assert bad["outcome"] == "error" and bad["error"] == "ValueError"


def test_old_days_are_deleted_on_start(tmp_path):
    (tmp_path / "2026-09-01.jsonl").write_text("{}\n")
    (tmp_path / "2026-10-05.jsonl").write_text("{}\n")
    Tracer(tmp_path, clock=Clock(), retention_days=14)
    assert not (tmp_path / "2026-09-01.jsonl").exists() and (tmp_path / "2026-10-05.jsonl").exists()


def test_new_day_new_file(tmp_path):
    clock = Clock()
    t = Tracer(tmp_path, clock=clock)
    t.event("a")
    clock.now += 86400
    t.event("b")
    t.close()
    assert sorted(p.name for p in tmp_path.glob("*.jsonl")) == ["2026-10-10.jsonl", "2026-10-11.jsonl"]


def test_read_events_skips_damaged_lines(tmp_path):
    (tmp_path / "2026-10-10.jsonl").write_text('{"kind":"a"}\nnot json\n{"kind":"b"}\n')
    assert [e["kind"] for e in read_events(tmp_path, "2026-10-10")] == ["a", "b"]
    assert read_events(tmp_path, "2020-01-01") == []


def test_null_tracer_has_the_same_api_and_writes_nothing(tmp_path):
    t = NullTracer()
    t.event("x", a=1)
    with t.span("y") as data:
        data["z"] = 1
    t.flush()
    t.close()
    assert list(tmp_path.iterdir()) == []


def test_one_event_is_cheap(tmp_path):
    t = Tracer(tmp_path, secret_values=lambda: ["s" * 20] * 5, clock=Clock())
    start = time.perf_counter()
    for i in range(500):
        t.event("tool_call", name="x", args={"q": "hello world"}, ms=1.0)
    t.close()
    assert (time.perf_counter() - start) / 500 < 0.001


def test_token_counts_are_kept_but_token_values_are_masked(tmp_path):
    t = Tracer(tmp_path, clock=Clock())
    t.event("llm_call", tokens_in=3000, cached_tokens=2500, reasoning_tokens=40, access_token="abc123xyz",
            token="zzz999zzz", api_key="k")
    t.close()
    [r] = lines(tmp_path)
    assert (r["tokens_in"], r["cached_tokens"], r["reasoning_tokens"]) == (3000, 2500, 40)
    assert r["access_token"] == r["token"] == r["api_key"] == "…"


def test_a_latency_field_with_token_in_its_name_is_a_number_not_a_secret(tmp_path):
    """first_token_ms was masked as a credential for as long as traces existed, so every first-token latency was lost."""
    import json

    from minty_core.trace import Tracer

    t = Tracer(tmp_path)
    t.event("llm_call", first_token_ms=412.5, time_to_first_token_ms=300.0, tokens_in=10, access_token="abc123",
            api_token="zzz", token="xyz", refresh_token="r", secret_key="k")
    t.close() if hasattr(t, "close") else None
    line = [ln for f in tmp_path.glob("*.jsonl") for ln in f.read_text().splitlines()][-1]
    e = json.loads(line)
    assert e["first_token_ms"] == 412.5 and e["time_to_first_token_ms"] == 300.0 and e["tokens_in"] == 10
    for secret in ("access_token", "api_token", "token", "refresh_token", "secret_key"):
        assert e[secret] == "…", secret  # real credentials are still masked
