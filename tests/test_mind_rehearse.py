"""A change is tried beside the current version before it is adopted: same cases, simulated where it must be, adopted only when nothing got worse (flows rehearse)."""
import json

import pytest

from tests.test_flows_acceptance import World, doc


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


TWICE = doc("lookups", [("go", "trigger.manual", {}), ("a", "tool.call", {"tool": "notes_find", "args": {"q": "x"}}), ("b", "tool.call", {"tool": "notes_find", "args": {"q": "x"}}),
                        ("r", "out.result", {"value": "{{nodes.b.text}}"})], [("go", "out", "a"), ("a", "out", "b"), ("b", "out", "r")])
ONCE = [{"op": "remove_node", "id": "b"}, {"op": "connect", "from": "a", "to": "r"}, {"op": "set_params", "id": "r", "params": {"value": "{{nodes.a.text}}"}}]


async def test_a_candidate_with_the_same_results_for_fewer_calls_is_proved_better_and_only_then_adopted(w):
    await w.tool("flows_save", doc=TWICE)
    held = await w.tool("flows_rehearse", flow="lookups", ops=ONCE)  # no apply: a rehearsal changes nothing
    assert held["verdict"] == "improved" and held["applied"] is False and held["calls"] == {"before": 2, "after": 1} and "simulated" in held["evidence"]
    assert w.svc.store.get("lookups").active_version == 1 and [v["status"] for v in w.svc.store.versions(1)] == ["active", "draft"]  # the candidate waits as a draft
    done = await w.tool("flows_rehearse", flow="lookups", ops=ONCE, apply=True)
    assert done["applied"] is True and w.svc.store.get("lookups").active_version == 3 and done["next"] == "adopted"
    assert (await w.tool("flows_run", flow="lookups"))["output"] == "notes_find ok"  # and it still gives the same answer


async def test_a_candidate_that_changes_what_comes_out_or_breaks_is_not_adopted_even_if_asked(w):
    await w.tool("flows_save", doc=TWICE)
    different = await w.tool("flows_rehearse", flow="lookups", apply=True, ops=[{"op": "set_params", "id": "r", "params": {"value": "something else"}}])
    assert different["verdict"] == "changed behaviour" and different["applied"] is False and w.svc.store.get("lookups").active_version == 1
    broken = await w.tool("flows_rehearse", flow="lookups", apply=True, ops=[{"op": "add_node", "id": "m", "type": "data.map", "params": {"list": "{{nodes.a.nothing}}", "fields": {"x": "1"}}},
                                                                            {"op": "connect", "from": "a", "to": "m"}, {"op": "connect", "from": "m", "to": "r"}])
    assert broken["verdict"] == "regression" and broken["applied"] is False  # the candidate fails where the current version works
    invalid = await w.tool("flows_rehearse", flow="lookups", ops=[{"op": "connect", "from": "r", "to": "a"}])
    assert invalid["verdict"] == "rejected" and any("circle" in i for i in invalid["issues"]) and len(w.svc.store.versions(1)) == 3  # a flawed candidate is not even kept as a draft


async def test_an_injected_failure_shows_whether_the_change_really_handles_errors(w):
    fragile = doc("fragile", [("go", "trigger.manual", {}), ("a", "tool.call", {"tool": "notes_find"}), ("r", "out.result", {"value": "{{nodes.a.text}}"})], [("go", "out", "a"), ("a", "out", "r")])
    await w.tool("flows_save", doc=fragile)
    handled = [{"op": "add_node", "id": "fb", "type": "out.result", "params": {"value": "the lookup is down"}}, {"op": "connect", "from": "a", "port": "error", "to": "fb"}]
    cases = [{"input": {}}, {"input": {}, "fail": {"a": "503 service unavailable"}}]
    r = await w.tool("flows_rehearse", flow="fragile", ops=handled, cases=cases, apply=True)
    assert r["verdict"] == "fixes a failure" and r["applied"] is True and "failures were injected, not real" in r["evidence"]
    assert [x["result"] for x in r["results"]] == ["same", "better"]  # normal runs are unchanged, the injected failure is now survived


async def test_a_replay_uses_what_a_real_run_recorded_so_nothing_is_sent_again(w):
    send = doc("report", [("go", "trigger.manual", {}), ("g", "tool.call", {"tool": "notes_find"}), ("s", "tool.call", {"tool": "mail_send", "args": {"to": "x"}}),
                          ("r", "out.result", {"value": "{{nodes.s.text}}"})], [("go", "out", "g"), ("g", "out", "s"), ("s", "out", "r")])
    await w.tool("flows_save", doc=send)
    real = await w.tool("flows_run", flow="report")
    sent = [c for c in w.calls if c[0] == "mail_send"]
    assert real["status"] == "done" and len(sent) == 1  # the real run sent once
    rehearsed = await w.tool("flows_rehearse", flow="report", replay=real["run"], ops=[{"op": "set_params", "id": "g", "params": {"retry": {"max": 1, "delay_s": 0}}}])
    assert [c for c in w.calls if c[0] == "mail_send"] == sent and len(w.calls) == 2  # no tool ran in the rehearsal: the recorded answers were used
    outputs = [r["output"] for r in w.svc.store.runs(1, 10) if r["trigger"] == "rehearsal"]
    assert outputs == ["mail_send ok", "mail_send ok"]  # both versions got the recorded answer of the send, which was not run again
    assert "recorded run" in rehearsed["evidence"] and "no tool ran" in rehearsed["evidence"] and rehearsed["verdict"] in ("equal", "improved")


async def test_a_rehearsal_with_no_recorded_run_only_reads_for_real_and_says_what_was_only_assessed(w):
    send = doc("report2", [("go", "trigger.manual", {}), ("g", "tool.call", {"tool": "notes_find"}), ("s", "tool.call", {"tool": "mail_send", "args": {"to": "x"}})], [("go", "out", "g"), ("g", "out", "s")])
    await w.tool("flows_save", doc=send)
    r = await w.tool("flows_rehearse", flow="report2", ops=[{"op": "set_params", "id": "g", "params": {"timeout_s": 30}}])
    assert [c[0] for c in w.calls] == ["notes_find", "notes_find"]  # the read ran for both versions, the send was never run
    assert "only assessed (simulated)" in r["evidence"] and r["verdict"] == "equal"
