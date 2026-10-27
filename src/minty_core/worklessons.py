"""What the record of missions and coding jobs says about how Minty is doing.

Plain counting over what really happened (the missions' ledgers, the coding jobs' verdicts). A finding is evidence with its
numbers; a proposal says what could change. A single bad run is never more than a candidate, a finding is confirmed only when
it repeats across separate jobs, and nothing here changes anything: settings, prompts and permissions stay the user's to change.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

MIN_CALLS = 4  # fewer calls of a tool than this say nothing about it
MIN_JOBS = 3  # a finding repeated in fewer separate jobs than this is only a candidate
FAIL_SHARE = 0.5


@dataclass(frozen=True)
class Lesson:
    key: str
    finding: str  # what happened, with the numbers
    proposal: str  # what could change (never applied by itself)
    confidence: str  # candidate | confirmed
    evidence: int  # how many separate jobs it was seen in


def _confidence(jobs: int) -> str:
    return "confirmed" if jobs >= MIN_JOBS else "candidate"


def tool_lessons(missions: list[dict]) -> list[Lesson]:
    calls, failed, seen_in, failed_in = Counter(), Counter(), defaultdict(set), defaultdict(set)
    for job in missions:
        for entry in (job.get("checkpoint") or {}).get("ledger") or []:
            tool = entry.get("tool")
            if not tool:
                continue
            calls[tool] += 1
            seen_in[tool].add(job["id"])
            if not entry.get("ok", True):
                failed[tool] += 1
                failed_in[tool].add(job["id"])
    out = []
    for tool, n in sorted(calls.items()):
        if n < MIN_CALLS or failed[tool] / n < FAIL_SHARE or len(failed_in[tool]) < 2:
            continue
        out.append(Lesson(f"tool:{tool}", f"{tool} failed {failed[tool]} of {n} times, in {len(failed_in[tool])} missions",
                          f"Try another source for what {tool} is used for, or check whether it needs a key or a setting.",
                          _confidence(len(failed_in[tool])), len(failed_in[tool])))
    return out


def delegation_lessons(jobs: list[dict]) -> list[Lesson]:
    by_project = defaultdict(list)
    for job in jobs:
        by_project[job.get("project", "?")].append(job)
    out = []
    for project, items in sorted(by_project.items()):
        n = len(items)
        retried = [j for j in items if j.get("rounds", 1) > 1 or j.get("verdict") == "checks_failed"]
        if len(retried) >= 2 and len(retried) / n >= FAIL_SHARE:
            out.append(Lesson(f"delegation:{project}:rounds",
                              f"On {project} the first try left the tests failing in {len(retried)} of {n} jobs",
                              "Put the failing test command and the files that matter into the request, or ask for a plan first.",
                              _confidence(len(retried)), len(retried)))
        unchecked = [j for j in items if j.get("verdict") == "unchecked"]
        if len(unchecked) >= 2:
            out.append(Lesson(f"delegation:{project}:unchecked", f"{len(unchecked)} of {n} jobs on {project} could not be checked",
                              "Set the agents setting check_command (for example pytest -q) so the tests run after each job.",
                              _confidence(len(unchecked)), len(unchecked)))
        silent = [j for j in items if j.get("verdict") == "no_changes"]
        if len(silent) >= 2:
            out.append(Lesson(f"delegation:{project}:no_changes",
                              f"{len(silent)} of {n} jobs on {project} said they were done and changed no files",
                              "Make the request more specific about which files or behaviour to change, or use mode ask for questions.",
                              _confidence(len(silent)), len(silent)))
    return out


def mission_lessons(missions: list[dict]) -> list[Lesson]:
    n = len(missions)
    if n < MIN_JOBS:
        return []
    out = []
    ran_out = [j for j in missions if j["status"] in ("failed", "timeout") and "ran out" in (j.get("result") or "")]
    if len(ran_out) >= 2:
        out.append(Lesson("missions:budget", f"{len(ran_out)} of {n} missions ran out of steps or time",
                          "Raise the missions setting max_steps or max_runtime_s, or hand over smaller goals.",
                          _confidence(len(ran_out)), len(ran_out)))
    blocked = [j for j in missions if j["status"] == "blocked"]
    if len(blocked) >= MIN_JOBS and len(blocked) / n >= FAIL_SHARE:
        out.append(Lesson("missions:blocked", f"{len(blocked)} of {n} missions stopped to ask you something",
                          "Say the details up front (dates, choices, which account) so fewer missions have to stop.",
                          "confirmed", len(blocked)))
    unverified = [j for j in missions if j["status"] == "done" and not (j.get("checkpoint") or {}).get("verified")]
    if len(unverified) >= 2:
        out.append(Lesson("missions:unverified", f"{len(unverified)} of {n} missions finished without being double-checked",
                          "Check that a model is available for the checking step; those results deserve a second look.",
                          _confidence(len(unverified)), len(unverified)))
    return out


def render(lessons: list[Lesson]) -> str:
    if not lessons:
        return "Not enough history yet to say what could work better."
    lines = [f"- [{l.confidence}] {l.finding}. Proposal: {l.proposal}" for l in lessons]
    return "What the record shows:\n" + "\n".join(lines) + "\nNothing was changed; these are proposals for you to decide."
