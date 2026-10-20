"""Heartbeat file, the 'alive' time and the weekly health digest (W1B-04)."""
import json
from datetime import datetime, timedelta

from minty_core.heartbeat import Heartbeat, digest, due_alive, gather


def write_day(folder, day, events):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{day}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))


def test_the_heartbeat_ages_and_a_missing_one_is_none(tmp_path):
    clock = {"t": 1000.0}
    hb = Heartbeat(tmp_path / "heartbeat.json", lambda: clock["t"])
    assert hb.age() is None
    hb.beat()
    clock["t"] += 90
    assert hb.age() == 90.0


def test_the_alive_message_goes_once_a_day_at_the_chosen_time_and_never_if_unset():
    now = datetime(2026, 10, 14, 8, 5)
    assert due_alive(now, "08:00", "2026-10-13") is True
    assert due_alive(now, "08:00", "2026-10-14") is False
    assert due_alive(datetime(2026, 10, 14, 7, 59), "08:00", "") is False
    assert due_alive(now, "", "") is False


def test_the_digest_counts_what_happened(tmp_path):
    now = datetime(2026, 10, 14, 18, 0)
    today, earlier = now.strftime("%Y-%m-%d"), (now - timedelta(days=2)).strftime("%Y-%m-%d")
    write_day(tmp_path / "t", today, [
        {"kind": "turn", "outcome": "answered", "first_audio_ms": 6200},
        {"kind": "turn", "outcome": "answered", "first_audio_ms": 3100},
        {"kind": "llm_call", "outcome": "error", "error_kind": "rate_limit"},
        {"kind": "llm_call", "outcome": "error", "error_kind": "rate_limit"},
        {"kind": "llm_call", "outcome": "ok"},
        {"kind": "tool_call", "ok": False},
        {"kind": "loop_stall", "ms": 900}])
    write_day(tmp_path / "t", earlier, [{"kind": "turn", "outcome": "answered", "first_audio_ms": 9000},
                                         {"kind": "llm_call", "outcome": "error", "error_kind": "timeout"}])
    text, facts = digest(trace_dir=tmp_path / "t", now=now, starts=9, unclean_starts=1,
                         providers=[{"name": "groq", "health": "ok"}, {"name": "nvidia", "cooling_for": 30.0}],
                         disk_free_mb=900.0, last_backup=(now - timedelta(days=5)).timestamp(), interrupted_jobs=2)
    assert "answered 3 requests" in text and "1 unclean start out of 9" in text
    assert "failed 3 times (2 rate limit, 1 timeout)" in text and "1 tool calls failed" in text
    assert "stalled 1 times" in text and "Providers having trouble: nvidia" in text
    assert "2 background jobs were interrupted" in text and "0.9 GB free" in text
    assert "5 days ago — that is too old" in text and facts["slowest_first_audio_ms"] == 9000.0


def test_a_quiet_healthy_week_says_so(tmp_path):
    now = datetime(2026, 10, 14, 18, 0)
    text, _ = digest(trace_dir=tmp_path / "none", now=now, starts=3, unclean_starts=0, providers=[],
                     disk_free_mb=50_000.0, last_backup=now.timestamp() - 3600, interrupted_jobs=0)
    assert "Everything looks healthy" in text and "today" in text
    assert gather(tmp_path / "none", now) == {"counts": {}, "model_errors": {}, "slowest_first_audio_ms": 0.0}
