"""WP-21 acceptance: the weekly report on a replayed day reproduces the known failure clusters; the panel is
unreachable from outside the LAN."""
from datetime import date

from fastapi.testclient import TestClient

from minty_core.selfcheck.report import load_week, weekly_report
from minty_core.trace import Tracer
from minty_panel.auth import PasswordStore
from minty_panel.main import build_panel_app

TODAY = date(2026, 10, 18)
STALE = "Error: unknown element; call browser_look and use a fresh [eN] reference"


def test_a_day_written_by_the_real_tracer_and_replayed_gives_the_known_clusters(tmp_path):
    t = Tracer(tmp_path / "traces")
    for i in range(5):
        t.event("tool_call", name="browser_click", ok=False, error=STALE.replace("eN", f"e{i + 3}"))
    for _ in range(4):
        t.event("llm_call", provider="groq", outcome="error", error_kind="rate_limit", error="429", tokens_in=0, tokens_out=0)
    for _ in range(3):
        t.event("turn", mode="classic", outcome="answered", total_ms=22000, first_audio_ms=9500)
    t.event("tool_call", name="files_read", ok=False, error="Error: FileNotFoundError: x")  # once: not a cluster
    for _ in range(40):
        t.event("turn", mode="classic", outcome="answered", total_ms=2500, first_audio_ms=1200)
    t.flush()
    t.close()
    import time

    day = time.strftime("%Y-%m-%d")
    report = weekly_report(load_week(tmp_path / "traces", date.fromisoformat(day)), date.fromisoformat(day))
    found = {(c.kind, c.key.split(" | ")[0]): c.count for c in report.clusters}
    assert found == {("tool", "browser_click"): 5, ("provider", "groq"): 4, ("slow", "slow turns"): 3}
    assert report.turns == 43


def test_the_real_panel_answers_your_network_and_nobody_else(tmp_path):
    PasswordStore(tmp_path).set("correct horse")
    (tmp_path / "panel.toml").write_text('host = "0.0.0.0"\nport = 7788\n')  # reachable on every interface...
    kw = build_panel_app(tmp_path)
    assert kw["host"] == "0.0.0.0"
    app = kw["app"]
    host = {"host": "192.168.1.20:7788"}
    for addr, ok in (("192.168.1.50", True), ("10.0.0.7", True), ("127.0.0.1", True), ("203.0.113.9", False),
                     ("198.51.100.4", False), ("2001:db8::5", False)):
        res = TestClient(app, client=(addr, 4000)).get("/", headers=host)
        assert (res.status_code == 403) is (not ok), (addr, res.status_code)
