"""Turn correlation ids (W0-02): every event of one turn is joinable by turn_id."""
import asyncio
import json
import re

from tests.fakes import FakeAgent
from tests.test_pipeline import build, finish, wait_until
from tests.test_router import P, make_router
from minty_core.history import ConversationArchive
from minty_core.llm.types import Message, TextDelta, ToolSpec
from minty_core.trace import (Tracer, annotate_turn, current_turn_id, read_events, transcript_hash,
                               turn_scope)
from minty_core.tracereport import format_report, summarise


def events(directory):
    return [json.loads(l) for p in sorted(directory.glob("*.jsonl")) for l in p.read_text().splitlines()]


def test_events_inside_a_turn_share_its_id_and_outside_have_none(tmp_path):
    t = Tracer(tmp_path)
    t.event("before")
    with turn_scope() as turn:
        t.event("a")
        t.event("b", turn_id="explicit")  # an explicit id wins
    t.event("after")
    t.close()
    before, a, b, after = events(tmp_path)
    assert "turn_id" not in before and "turn_id" not in after
    assert a["turn_id"] == turn["turn_id"] and re.fullmatch(r"[0-9a-f]{12}", a["turn_id"])
    assert b["turn_id"] == "explicit"


async def test_tasks_started_inside_a_turn_inherit_its_id():
    with turn_scope() as turn:
        seen = await asyncio.create_task(asyncio.sleep(0, result=None)) or current_turn_id()
        inner = await asyncio.create_task(_tid())
    assert inner == seen == turn["turn_id"] and current_turn_id() is None


async def _tid():
    return current_turn_id()


def test_nested_scope_keeps_the_outer_turn():
    with turn_scope() as outer:
        with turn_scope() as inner:
            assert inner["turn_id"] == outer["turn_id"]
        assert current_turn_id() == outer["turn_id"]


def test_transcript_hash_is_short_and_reveals_no_words():
    h = transcript_hash("Click on phones")
    assert re.fullmatch(r"[0-9a-f]{12}", h) and h == transcript_hash("  click on PHONES ")
    assert "phones" not in h and transcript_hash("") == ""


def test_offered_tool_names_are_kept_whole(tmp_path):
    t = Tracer(tmp_path)
    t.event("llm_call", tool_names=[f"tool_{i}" for i in range(50)])
    t.close()
    [e] = events(tmp_path)
    assert len(e["tool_names"]) == 50


async def test_llm_call_carries_turn_id_tools_toolkits_and_hash(tmp_path):
    router, _ = make_router(tmp_path, [P("a")], ["a"], {"a": [TextDelta("ok")]})
    router.tracer = Tracer(tmp_path / "traces", secret_values=lambda: ["key-a-secret-value"])
    tools = [ToolSpec("browser_click", "click", {}), ToolSpec("clock_get_time", "time", {})]
    with turn_scope() as turn:
        annotate_turn(toolkits=["browser"], transcript_hash=transcript_hash("click store"))
        [_ async for _ in router.stream([Message("user", "click store")], tools)]
    router.tracer.close()
    [call] = [e for e in events(tmp_path / "traces") if e["kind"] == "llm_call"]
    assert call["turn_id"] == turn["turn_id"]
    assert call["tool_names"] == ["browser_click", "clock_get_time"] and call["toolkits"] == ["browser"]
    assert call["transcript_hash"] == transcript_hash("click store")
    assert "click store" not in json.dumps(call)


async def test_llm_call_outside_a_turn_has_no_turn_fields(tmp_path):
    router, _ = make_router(tmp_path, [P("a")], ["a"], {"a": [TextDelta("ok")]})
    router.tracer = Tracer(tmp_path / "traces")
    [_ async for _ in router.stream([Message("user", "x")], [])]
    router.tracer.close()
    [call] = events(tmp_path / "traces")
    assert "turn_id" not in call and "toolkits" not in call


def test_history_rows_carry_the_turn_id(tmp_path):
    archive = ConversationArchive(tmp_path)
    archive.log("user", "outside")
    with turn_scope() as turn:
        archive.log("user", "inside")
    rows = [json.loads(l) for p in (tmp_path / "raw").glob("*.jsonl") for l in p.read_text().splitlines()]
    assert "turn_id" not in rows[0] and rows[1]["turn_id"] == turn["turn_id"]


class TracingAgent(FakeAgent):
    """A fake agent that records a model call and a tool call the way the real ones do."""

    def __init__(self, replies, tracer):
        super().__init__(replies)
        self.tracer = tracer

    async def respond(self, text, *, sensitive=False):
        self.tracer.event("llm_call", provider="fake", outcome="ok")
        self.tracer.event("tool_call", name="clock_get_time", ok=True)
        async for sentence in super().respond(text, sensitive=sensitive):
            yield sentence


async def test_every_event_of_a_voice_turn_shares_one_id(tmp_path):
    p, f = build(tmp_path, stt_texts=["one", "two"])
    tracer = Tracer(tmp_path / "traces")
    p.agent = f.agent = TracingAgent([["First."], ["Second."]], tracer)
    p.tracer = tracer
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    task = asyncio.create_task(p.run())
    await wait_until(lambda: b"First." in f.player.played)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: b"Second." in f.player.played)
    await wait_until(lambda: sum(1 for e in read_events(tmp_path / "traces") if e["kind"] == "turn") == 2)
    await finish(p, f, task)
    tracer.close()
    evs = [e for e in events(tmp_path / "traces") if e["kind"] in ("turn", "llm_call", "tool_call")]
    ids = [e.get("turn_id") for e in evs]
    assert all(ids) and len(set(ids)) == 2
    for tid in set(ids):
        assert sorted(e["kind"] for e in evs if e["turn_id"] == tid) == ["llm_call", "tool_call", "turn"]


def test_report_groups_events_per_turn():
    evs = [
        {"kind": "turn", "turn_id": "t1", "outcome": "answered", "total_ms": 900.0},
        {"kind": "llm_call", "turn_id": "t1", "provider": "groq", "outcome": "error"},
        {"kind": "llm_call", "turn_id": "t1", "provider": "nvidia", "outcome": "ok"},
        {"kind": "tool_call", "turn_id": "t1", "name": "browser_click", "ok": True},
        {"kind": "turn", "turn_id": "t2", "outcome": "barged", "total_ms": 7000.0},
        {"kind": "llm_call", "turn_id": "t2", "provider": "mistral", "outcome": "error"},
        {"kind": "llm_call", "turn_id": "t2", "provider": "gemini", "outcome": "error"},
        {"kind": "llm_call", "provider": "groq", "outcome": "ok"},  # no id: not grouped
    ]
    s = summarise(evs)
    assert s["turns_traced"] == 2
    worst = s["worst_turns"][0]
    assert worst["turn_id"] == "t2" and worst["failed_calls"] == 2 and worst["outcome"] == "barged"
    assert worst["failed_providers"] == ["mistral", "gemini"] and worst["llm_calls"] == 2
    assert "t2" in format_report(s)
