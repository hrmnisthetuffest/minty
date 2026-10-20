"""Per-tool hourly limits and the autonomy budget (W1-13)."""
from types import SimpleNamespace

from tests.safety_helpers import build, call, say
from minty_core.config import SafetyConfig
from minty_core.safety import SafetyKit
from minty_core.secretscan import SecretScanner
from minty_core.toollimits import ToolLimits


class Clock:
    def __init__(self):
        self.now = 10_000.0

    def __call__(self):
        return self.now


def limits(clock=None, **safety):
    store = SimpleNamespace(config=SimpleNamespace(safety=SafetyConfig(**safety)))
    return ToolLimits(store, clock or Clock())


def test_the_default_limit_stops_a_loop_of_changes_and_resets_with_the_hour():
    clock = Clock()
    lim = limits(clock, default_hourly_limit_changes=5, autonomy_budget_per_hour=0)
    for _ in range(5):
        assert lim.check("files_write", 2) is None
        lim.record("files_write", 2)
    reason = lim.check("files_write", 2)
    assert reason and "5 times in the last hour" in reason and "looks like a loop" in reason
    clock.now += 3601
    assert lim.check("files_write", 2) is None


def test_reading_is_never_limited_and_tools_are_counted_separately():
    lim = limits(default_hourly_limit_changes=2, autonomy_budget_per_hour=0)
    for _ in range(50):
        lim.record("web_search_search", 0)
    assert lim.check("web_search_search", 0) is None
    lim.record("files_write", 2)
    lim.record("files_write", 2)
    assert lim.check("files_write", 2) is not None and lim.check("files_delete", 2) is None


def test_a_named_limit_beats_the_default_and_patterns_work():
    lim = limits(default_hourly_limit_changes=100, autonomy_budget_per_hour=0, tool_hourly_limits={"mail_*": 1})
    lim.record("mail_send", 2)
    assert lim.check("mail_send", 2) is not None
    assert lim.check("mail_reply", 2) is None  # counted per tool name: another tool starts at zero
    assert limits(tool_hourly_limits={"files_*": 0}).check("files_write", 2) is None  # 0 = no limit


def test_the_autonomy_budget_counts_every_change():
    lim = limits(default_hourly_limit_changes=0, autonomy_budget_per_hour=3)
    for name in ("a_do", "b_do", "c_do"):
        lim.record(name, 1)
    reason = lim.check("d_do", 1)
    assert reason and "3 changes on my own" in reason
    lim.reset()
    assert lim.check("d_do", 1) is None


def kit(store):
    kit_ = SafetyKit(store, SecretScanner())
    return kit_


async def test_a_runaway_loop_is_stopped_in_the_agent_with_a_spoken_reason(tmp_path):
    rounds = [[call("files_write", {"path": f"{i}.txt"}, ident=str(i))] for i in range(6)]
    agent, host, _ = build(tmp_path, {"files_write": (2, None)}, rounds,
                           settings={"safety": {"default_hourly_limit_changes": 3, "autonomy_budget_per_hour": 0}},
                           safety=kit)
    agent._max_rounds = 8
    await say(agent, "write lots of files")
    assert len(host.called) == 3
    blocked = [m.content for m in agent.history if m.role == "tool" and m.content.startswith("Blocked")]
    assert blocked and "looks like a loop" in blocked[0]
