"""The health tools and the one-phrase controls (LF-01, LF-02, 965, 984)."""
from datetime import datetime, timedelta, timezone

from tests.plugin_helpers import make_host
from minty_core.commands import CommandRouter
from minty_core.health import HealthCommands, HealthHub

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def at(days_ago):
    return (NOW - timedelta(days=days_ago)).timestamp()


def hub(tmp_path, grant=True):
    h = HealthHub(tmp_path / "health.db", clock=NOW.timestamp)
    if grant:
        for d in ("training", "body", "sleep", "nutrition", "activity"):
            h.grant(d)
    return h


def seeded(tmp_path):
    h = hub(tmp_path)
    for i in range(3):
        h.add_workout("hevy", f"w{i}", at(i + 1), f"Session {i}", 3600,
                      [{"exercise": "Squat", "idx": 0, "weight_kg": 80 + i, "reps": 5}])
    h.add_body("health_connect", "b", at(1), "weight", 62.4, "kg")
    return h


def host(tmp_path, h):
    return make_host(tmp_path / "cfg", services={"health": h})


async def test_the_summary_is_a_small_sensitive_facts_pack(tmp_path):
    h = seeded(tmp_path)
    plugin = host(tmp_path, h)
    out = await plugin.call("health_summary", {"days": 7})
    assert "3 workouts" in out and len(out) < 900
    assert plugin.get_tool("health_summary").sensitive is True
    assert "Session 1" not in out  # no row-level detail reaches the model


async def test_showing_rows_is_for_your_eyes_only(tmp_path):
    h = seeded(tmp_path)
    plugin = host(tmp_path, h)
    tool = plugin.get_tool("health_show")
    assert tool.local_only is True and tool.sensitive is True
    out = await plugin.call("health_show", {"domain": "training", "days": 14})
    assert "Session 1" in out and "Squat" in out and out.count("\n") <= 14


async def test_status_profile_and_consent_by_tool(tmp_path):
    h = hub(tmp_path, grant=False)
    plugin = host(tmp_path, h)
    assert "sleep: off" in await plugin.call("health_status", {})
    assert "now collecting" in await plugin.call("health_consent", {"domain": "sleep"})
    assert h.consent("sleep") is True
    assert "stopped collecting sleep" in await plugin.call("health_consent", {"domain": "sleep", "allow": False})
    assert h.consent("sleep") is False
    assert "age is 15" in await plugin.call("health_set_profile", {"key": "age", "value": "15"})
    assert (await plugin.call("health_set_profile", {"key": "age", "value": "3"})).startswith("Error")
    assert "age: 15" in await plugin.call("health_status", {})


async def test_deleting_is_the_highest_risk_class_and_names_what_goes(tmp_path):
    h = seeded(tmp_path)
    plugin = host(tmp_path, h)
    tool = plugin.get_tool("health_delete_data")
    assert tool.risk == 3
    assert "3" in tool.describe({"domain": "training"})
    assert "Deleted 3" in await plugin.call("health_delete_data", {"domain": "training"})
    assert h.counts()["training"] == 0


async def test_quality_and_hevy_report_tools_say_they_changed_nothing(tmp_path):
    h = hub(tmp_path)
    h.add_body("health_connect", "b", at(1), "weight", 6.2, "kg")
    plugin = host(tmp_path, h)
    assert "haven't changed anything" in await plugin.call("health_check", {})
    assert "nothing" in (await plugin.call("health_check", {"what": "hevy"})).lower()


def test_one_phrase_stops_collection_and_deleting_needs_a_second_yes(tmp_path):
    h = seeded(tmp_path)
    cmds = HealthCommands(h)
    reply = cmds.handle("stop collecting my health data")
    assert "stopped" in reply.lower() and not any(h.consents().values()) and h.counts()["training"] == 3
    assert "3 workouts" in cmds.handle("delete my workout data") or "3" in cmds.handle("delete my workout data")
    assert h.counts()["training"] == 3  # asked, not done
    assert "Deleted 3" in cmds.handle("yes delete my workout data")
    assert h.counts()["training"] == 0
    cmds.handle("delete my sleep data")
    assert cmds.handle("what time is it") is None  # anything else cancels
    assert cmds.handle("yes delete my sleep data") is not None  # but the explicit full phrase still works
    assert cmds.handle("what's the weather") is None


def test_the_command_router_hands_the_phrase_to_the_hub(tmp_path):
    class Agent:
        history: list = []

    h = seeded(tmp_path)
    router = CommandRouter(Agent(), health_data=HealthCommands(h))
    assert "stopped" in router.handle("stop collecting my health data").lower()
    assert router.handle("what's the capital of France") is None


def test_the_telegram_chat_can_stop_collection_but_cannot_delete():
    import tempfile
    from pathlib import Path

    h = hub(Path(tempfile.mkdtemp()))
    cmds = HealthCommands(h)
    assert cmds.stop_phrase("delete my sleep data") is None  # not offered from a chat
    assert cmds.stop_phrase("stop collecting my health data") is not None
