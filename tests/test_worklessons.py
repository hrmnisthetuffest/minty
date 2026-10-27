"""What the record of missions and coding jobs says about how Minty is doing, as evidence and proposals, never as rules."""
from minty_core.worklessons import Lesson, delegation_lessons, mission_lessons, render, tool_lessons


def mission(i, ledger=(), status="done", result="", verified=True):
    return {"id": i, "status": status, "result": result, "checkpoint": {"ledger": list(ledger), "verified": verified}}


def call(tool, ok=True):
    return {"tool": tool, "ok": ok, "result": "x", "args": "{}"}


def coding(i, project="web", status="done", verdict="verified", rounds=1):
    return {"id": i, "project": project, "agent": "claude_code", "status": status, "verdict": verdict, "rounds": rounds}


def test_a_tool_that_keeps_failing_across_missions_is_a_confirmed_finding_with_numbers():
    jobs = [mission(0, [call("web_search_search")] * 2)] + [mission(i, [call("web_search_search", ok=False)] * 2) for i in (1, 2, 3)]
    jobs.append(mission(9, [call("notes_find")] * 4))
    out = tool_lessons(jobs)
    assert [l.key for l in out] == ["tool:web_search_search"]
    assert "6 of 8" in out[0].finding and out[0].confidence == "confirmed" and "another source" in out[0].proposal
    two = tool_lessons(jobs[:3])  # the same pattern in only two missions is a candidate
    assert two and two[0].confidence == "candidate"


def test_a_handful_of_failures_in_one_mission_is_noise_not_a_lesson():
    assert tool_lessons([mission(1, [call("web_search_search", ok=False)] * 5)]) == []
    assert tool_lessons([mission(1, [call("t", ok=False)] * 2), mission(2, [call("t", ok=False)])]) == []


def test_one_bad_project_run_is_only_a_candidate_and_never_a_rule():
    out = delegation_lessons([coding(1, verdict="checks_failed", status="failed", rounds=2)] * 1)
    assert out == [] or all(l.confidence == "candidate" for l in out)


def test_repeated_failing_tests_on_one_project_suggest_a_better_brief_or_check():
    jobs = [coding(i, verdict="verified", rounds=2) for i in range(4)] + [coding(9, verdict="verified", rounds=1)]
    out = {l.key: l for l in delegation_lessons(jobs)}
    lesson = out["delegation:web:rounds"]
    assert "4 of 5" in lesson.finding and lesson.confidence == "confirmed" and "failing" in lesson.proposal.lower()


def test_jobs_nobody_could_check_suggest_setting_the_test_command():
    out = {l.key: l for l in delegation_lessons([coding(i, verdict="unchecked") for i in range(3)])}
    assert "check_command" in out["delegation:web:unchecked"].proposal


def test_an_agent_that_keeps_claiming_without_changing_anything():
    out = {l.key: l for l in delegation_lessons([coding(i, status="failed", verdict="no_changes") for i in range(2)])}
    assert "specific" in out["delegation:web:no_changes"].proposal


def test_mission_outcomes_point_at_the_cause():
    jobs = ([mission(i, status="failed", result="I ran out of steps.") for i in range(3)]
            + [mission(5, status="blocked", result="Which one?"), mission(6, status="done"), mission(7, status="done")])
    out = {l.key: l for l in mission_lessons(jobs)}
    assert "3 of 6" in out["missions:budget"].finding and "max_steps" in out["missions:budget"].proposal
    assert "missions:blocked" not in out  # one question is normal


def test_missions_nobody_could_double_check_are_reported():
    jobs = [mission(i, verified=False) for i in range(3)] + [mission(9)]
    assert "3 of 4" in {l.key: l for l in mission_lessons(jobs)}["missions:unverified"].finding


def test_the_report_names_the_evidence_and_says_nothing_was_changed():
    text = render([Lesson("tool:t", "t failed 4 of 6 times in 3 missions", "use another source", "confirmed", 3)])
    assert "t failed 4 of 6" in text and "use another source" in text and "Nothing was changed" in text
    assert "Not enough" in render([])
