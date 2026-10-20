import json
import subprocess
import sys

from minty_core.tracereport import format_report, summarise

EVENTS = [
    {"kind": "turn", "mode": "classic", "outcome": "answered", "first_audio_ms": 900.0, "total_ms": 3000.0},
    {"kind": "turn", "mode": "classic", "outcome": "answered", "first_audio_ms": 1500.0, "total_ms": 4000.0},
    {"kind": "turn", "mode": "classic", "outcome": "command", "total_ms": 500.0},
    {"kind": "llm_call", "provider": "groq", "outcome": "error", "chars_in": 400},
    {"kind": "llm_call", "provider": "gemini", "outcome": "ok", "first_token_ms": 300.0, "chars_in": 400, "chars_out": 80},
    {"kind": "llm_call", "provider": "gemini", "outcome": "ok", "first_token_ms": 500.0, "chars_in": 400, "chars_out": 120},
    {"kind": "tool_call", "name": "clock_now", "ok": True},
    {"kind": "tool_call", "name": "browser_open", "ok": False},
    {"kind": "confirm", "tool": "x", "answer": False},
]


def test_summary_numbers():
    s = summarise(EVENTS)
    assert s["turns"] == 3 and s["outcomes"] == {"answered": 2, "command": 1}
    assert s["first_word_p50_ms"] in (900.0, 1500.0) and s["first_word_p95_ms"] == 1500.0
    assert s["llm_calls_per_turn"] == 1.0 and s["answers_by_provider"] == {"gemini": 2}
    assert s["failovers_by_provider"] == {"groq": 1} and s["first_token_median_ms"] == 400.0
    assert s["tool_error_rate"] == 0.5 and s["tool_errors"] == {"browser_open": 1}
    assert s["est_tokens"] == (1200 + 200) // 4 and s["confirmations"] == 1
    assert s["slowest_turns"][0]["total_ms"] == 4000.0


def test_empty_day_formats_without_errors():
    text = format_report(summarise([]))
    assert "Turns: 0" in text and "—" in text


def test_cli_reads_a_day(tmp_path):
    (tmp_path / "2026-10-10.jsonl").write_text("\n".join(json.dumps(e) for e in EVENTS) + "\n")
    out = subprocess.run([sys.executable, "scripts/trace_report.py", "--day", "2026-10-10", "--dir", str(tmp_path)],
                         capture_output=True, text=True, check=True).stdout
    assert "Turns: 3" in out and "gemini" in out


def test_old_traces_with_masked_latencies_still_make_a_report():
    """Traces written before the masking fix hold "…" in place of first_token_ms; the report skips them instead of crashing."""
    events = [{"kind": "llm_call", "provider": "groq", "outcome": "ok", "first_token_ms": "…", "chars_in": 100, "chars_out": 10},
              {"kind": "llm_call", "provider": "groq", "outcome": "ok", "first_token_ms": 250.0, "chars_in": 100, "chars_out": 10},
              {"kind": "llm_call", "provider": "groq", "outcome": "ok", "first_token_ms": "…", "chars_in": 100, "chars_out": 10}]
    s = summarise(events)
    assert s["first_token_median_ms"] == 250.0
    assert summarise([events[0]])["first_token_median_ms"] is None


STAGES = [
    {"kind": "turn", "mode": "classic", "outcome": "answered", "silence_ms": 900, "stt_ms": 400.0, "first_sentence_ms": 1500.0,
     "first_audio_ms": 1900.0, "total_ms": 3000.0},
    {"kind": "turn", "mode": "classic", "outcome": "answered", "silence_ms": 640, "stt_ms": 5.0, "first_sentence_ms": 1200.0,
     "first_audio_ms": 1500.0, "total_ms": 2500.0, "spec": "early"},
    {"kind": "turn", "mode": "classic", "outcome": "answered", "silence_ms": 900, "stt_ms": 8.0, "first_sentence_ms": 900.0,
     "first_audio_ms": 1100.0, "total_ms": 2000.0, "spec": "used"},
    {"kind": "turn", "mode": "classic", "outcome": "answered", "first_audio_ms": 300.0, "total_ms": 600.0},
    {"kind": "instant", "rule": "time now", "category": "time and date"},
    {"kind": "instant", "rule": "arithmetic", "category": "maths"},
]


def test_where_the_wait_goes_is_reported_per_stage():
    s = summarise(STAGES)
    assert s["silence_p50_ms"] == 900 and s["stt_p50_ms"] == 8.0 and s["first_sentence_p50_ms"] == 1200.0
    assert s["spec_turns"] == {"used": 1, "early": 1} and s["instant_answers"] == 2 and s["instant_share"] == 0.5
    text = format_report(s)
    assert "end-of-speech pause 900 ms" in text and "recognition 8" in text and "Instant answers (no model): 2" in text
    assert "guess used 1, early 1" in text


def test_no_stage_numbers_is_not_an_error():
    s = summarise(EVENTS)
    assert s["silence_p50_ms"] is None and s["instant_answers"] == 0 and s["spec_turns"] == {}
    assert "—" in format_report(s)
