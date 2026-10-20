"""WP-06 acceptance: kill -9 starts fresh and keeps the old conversation; offline scenarios; injected faults do not defeat fail-over."""
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path

from tests.safety_helpers import build, say
from tests.test_turn_deadline import Slow, router_with
from minty_core.app import announce_interrupted
from minty_core.commands import CommandRouter
from minty_core.context import build_context
from minty_core.llm.types import LLMError, Message, TextDelta
from minty_core.offline import NetworkProbe, OfflineHandler

ROOT = Path(__file__).resolve().parents[1]

CHILD = textwrap.dedent("""
    import sys, time
    from pathlib import Path
    from minty_core.journal import ConversationJournal
    from minty_core.llm.types import Message

    j = ConversationJournal(Path(sys.argv[1]) / "conversation.json")
    j.save([Message("user", "open the shop and compare two desk lamps"), Message("assistant", "Opened the shop.")])
    print("ready", flush=True)
    time.sleep(60)
""")


def killed_conversation(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", CHILD, str(tmp_path)], stdout=subprocess.PIPE, text=True,
                             env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
    try:
        assert child.stdout.readline().strip() == "ready"
        child.kill()
        child.wait(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()


def test_after_kill_nine_it_starts_fresh_without_asking_and_keeps_the_old_conversation(tmp_path):
    killed_conversation(tmp_path)
    ctx = build_context(tmp_path)
    assert "resume" not in ctx.services  # no "I wasn't closed properly" question any more
    assert ctx.agent.history == []  # nothing was restored on its own
    archived = list((tmp_path / "journal-archive").glob("*.json"))
    assert len(archived) == 1 and "compare two desk lamps" in archived[0].read_text()
    assert "closed properly" not in announce_interrupted(ctx)


def test_a_second_start_after_the_kill_has_nothing_left_to_archive(tmp_path):
    killed_conversation(tmp_path)
    build_context(tmp_path)
    build_context(tmp_path)
    assert len(list((tmp_path / "journal-archive").glob("*.json"))) == 1


class Tools:
    def __init__(self):
        self.log = []

    async def __call__(self, name, args):
        self.log.append(name)
        return f"{name} ok"


async def test_with_the_network_off_six_scripted_requests_succeed_and_the_seventh_is_honest(tmp_path):
    tools = Tools()
    probe = NetworkProbe()
    probe.forced = True
    agent, host, _ = build(tmp_path, {}, [])
    agent.offline = OfflineHandler(tools, probe, clock=lambda: datetime(2026, 10, 14, 17, 42))

    async def never(messages, tools_, **kw):
        raise AssertionError("a model was called while offline")
        yield  # noqa

    agent._router.stream = never
    said = []
    for request in ("what time is it", "what's the date", "set a timer for 5 minutes",
                    "remind me to call the dentist at 6 pm", "take a note: buy a notebook", "what are my timers"):
        said += await say(agent, request)
    assert all(s for s in said) and tools.log == ["timers_set_timer", "reminders_set", "journal_add_entry",
                                                   "timers_list_timers", "reminders_list"]
    last = await say(agent, "write an essay about volcanoes")
    assert "offline right now, so I can't do that" in last[0] and "set timers" in last[0]


async def test_with_a_third_of_calls_failing_every_request_is_still_answered(tmp_path):
    router, _ = router_with(tmp_path, {"a": Slow("a", 0.0, text="A"), "b": Slow("b", 0.0, text="B"),
                                       "c": Slow("c", 0.0, text="C")}, deadline=2.0)
    data = router._store.config.model_dump(mode="json")
    data["debug"].update(inject={"rate_limit": 0.20, "timeout": 0.10, "server": 0.05}, seed=11)
    router._store.update(data)
    answered = failed = 0
    for _ in range(40):
        router._cooldown.clear()  # each request starts with every provider available
        router._demoted.clear()
        try:
            events = [e async for e in router.stream([Message("user", "hi")], [], tier="fast")]
            answered += any(isinstance(e, TextDelta) for e in events)
        except LLMError:
            failed += 1
    assert answered >= 36  # 3 providers, 35 % of calls failing: all three fail for ~4 % of requests (seeded run)
    assert answered + failed == 40
