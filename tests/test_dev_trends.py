"""656: trace trends as sparklines and a chart page."""
import json
from datetime import date, timedelta

from minty_core.dev.trends import daily_metrics, sparkline, trend_svg, trend_text, write_chart

TODAY = date(2026, 10, 18)


def write_day(folder, day, first_audio, turns=3, failovers=0, tool_errors=0, tools=4):
    events = []
    for i in range(turns):
        events.append({"kind": "turn", "turn_id": f"{day}-{i}", "mode": "classic", "outcome": "answered",
                       "first_audio_ms": first_audio, "total_ms": first_audio + 800})
        events.append({"kind": "llm_call", "turn_id": f"{day}-{i}", "provider": "groq", "outcome": "ok",
                       "chars_in": 4000, "chars_out": 400})
    for i in range(failovers):
        events.append({"kind": "llm_call", "turn_id": f"{day}-0", "provider": "gemini", "outcome": "error"})
    for i in range(tools):
        events.append({"kind": "tool_call", "name": "clock_get_time", "ok": i >= tool_errors})
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{day}.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")


def seed(folder, slow_last_week=True):
    for back in range(13, -1, -1):
        day = TODAY - timedelta(days=back)
        if back == 5:
            continue  # a day with no traces
        write_day(folder, day.isoformat(), first_audio=2400 if (slow_last_week and back < 7) else 1200,
                  turns=2 + back % 3, failovers=1 if back == 2 else 0, tool_errors=1 if back == 0 else 0)


def test_daily_metrics_fill_missing_days_with_none(tmp_path):
    seed(tmp_path)
    days = daily_metrics(tmp_path, days=14, today=TODAY)
    assert len(days) == 14 and days[-1].date == TODAY and days[0].date == TODAY - timedelta(days=13)
    missing = next(d for d in days if d.date == TODAY - timedelta(days=5))
    assert missing.turns == 0 and missing.first_word_p50_ms is None
    assert days[-1].first_word_p50_ms == 2400 and days[0].first_word_p50_ms == 1200
    assert sum(d.failovers for d in days) == 1 and days[-1].tool_error_rate == 0.25


def test_sparklines_use_blocks_and_dots_for_gaps():
    assert sparkline([1, 2, 3, 4, 5, 6, 7, 8]) == "▁▂▃▄▅▆▇█"
    assert sparkline([None, 5, None]) == "·▄·"
    assert sparkline([3, 3, 3]) == "▄▄▄" and sparkline([]) == ""


def test_the_text_says_which_way_each_number_is_going(tmp_path):
    seed(tmp_path)
    text = trend_text(daily_metrics(tmp_path, 14, TODAY))
    assert "First word" in text and "slower" in text  # latency doubled this week
    assert "Turns" in text and "Fallbacks" in text and "Tool errors" in text
    assert all(len(line) < 140 for line in text.splitlines())
    calm = tmp_path / "calm"
    seed(calm, slow_last_week=False)
    assert "slower" not in trend_text(daily_metrics(calm, 14, TODAY))


def test_no_traces_at_all_is_said_plainly(tmp_path):
    assert trend_text(daily_metrics(tmp_path, 14, TODAY)) == "No traces yet, so there is nothing to chart."


def test_the_chart_is_a_script_free_svg_with_escaped_labels(tmp_path):
    seed(tmp_path)
    svg = trend_svg(daily_metrics(tmp_path, 14, TODAY))
    assert svg.startswith("<svg") and "<script" not in svg.lower() and "onload" not in svg.lower()
    assert svg.count("<polyline") >= 3 and "First word" in svg


def test_the_chart_page_is_written_owner_only_with_a_csp(tmp_path):
    seed(tmp_path / "traces")
    path = write_chart(tmp_path / "charts", daily_metrics(tmp_path / "traces", 14, TODAY), now=TODAY)
    assert path.name == "trends-20261018.html" and oct(path.stat().st_mode & 0o777) == "0o600"
    page = path.read_text(encoding="utf-8")
    assert "Content-Security-Policy" in page and "<svg" in page and "<script" not in page.lower()
