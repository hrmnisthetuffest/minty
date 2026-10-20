"""CI status from the GitHub CLI (649). Read-only (`gh run list`), and the watcher never asks more often than every five
minutes; it speaks when a workflow turns red or green again, not on every look. `gh` is not installed here: the module says
how to get it and works on `gh run list --json` output."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Callable

from minty_core.system.osinfo import OS, detect, hint_for
from minty_core.system.runner import Runner

FIELDS = "databaseId,status,conclusion,workflowName,headBranch,displayTitle,url"


def gh_rule(a: list[str]) -> bool:
    if a[1:3] == ["issue", "create"]:  # only the exact shape the idea tool builds, after your yes
        return (len(a) == 9 and a[3] == "--repo" and a[5] == "--title" and a[7] == "--body"
                and re.fullmatch(r"[A-Za-z0-9._-]{1,39}/[A-Za-z0-9._-]{1,100}", a[4]) is not None)
    return (a[1:3] == ["run", "list"] and "--json" in a) or (len(a) == 4 and a[1:3] == ["run", "view"] and a[3].isdigit()) \
        or a[1:3] == ["auth", "status"]


def make_runner() -> Runner:
    return Runner(allowed={"gh"}, rules={"gh": gh_rule}, timeout=30.0)


@dataclass
class Run:
    id: int
    status: str
    conclusion: str
    workflow: str
    branch: str
    title: str
    url: str


def parse_runs(text: str) -> list[Run]:
    try:
        rows = json.loads(text)
    except ValueError:
        return []
    return [Run(int(r.get("databaseId") or 0), str(r.get("status") or ""), str(r.get("conclusion") or ""),
                str(r.get("workflowName") or ""), str(r.get("headBranch") or ""), str(r.get("displayTitle") or ""),
                str(r.get("url") or "")) for r in rows if isinstance(r, dict)]


def _state(r: Run) -> str:
    if r.status != "completed":
        return "running"
    return {"success": "succeeded", "failure": "failed", "cancelled": "cancelled", "skipped": "skipped"}.get(
        r.conclusion, r.conclusion or "finished")


def latest_per_workflow(runs: list[Run]) -> list[Run]:
    seen, out = set(), []
    for r in runs:
        key = (r.workflow, r.branch)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def summarise_runs(runs: list[Run]) -> str:
    if not runs:
        return "No CI runs found."
    return "\n".join(f"{r.workflow} on {r.branch}: {_state(r)}" + (f" ({r.title[:60]})" if _state(r) == "failed" else "")
                     for r in latest_per_workflow(runs)[:4])


class CiWatcher:
    def __init__(self, runner, clock: Callable[[], float] = time.time, min_gap_s: float = 300.0,
                 os_info: OS | None = None):
        self.r, self._clock, self.min_gap = runner, clock, max(300.0, float(min_gap_s))
        self._os = os_info
        self._last_poll: dict[str, float] = {}
        self._seen: dict[tuple[str, str, str], str] = {}
        self._told = False

    async def list(self, cwd) -> list[Run]:
        res = await self.r.run(["gh", "run", "list", "--json", FIELDS, "--limit", "10"], cwd=str(cwd))
        return parse_runs(res.out) if res.ok else []

    async def poll(self, cwd, name: str) -> list[str]:
        if not self.r.have("gh"):
            if self._told:
                return []
            self._told = True
            return ["The GitHub CLI is not installed, so I cannot watch CI. " + hint_for(self._os or detect(), "gh")
                    + " Then run: gh auth login"]
        now = self._clock()
        if now - self._last_poll.get(name, -1e12) < self.min_gap and name in self._last_poll:
            return []
        self._last_poll[name] = now
        out = []
        for run in latest_per_workflow(await self.list(cwd)):
            if run.status != "completed":
                continue
            key = (name, run.workflow, run.branch)
            before, now_state = self._seen.get(key), run.conclusion
            self._seen[key] = now_state
            if before is None or before == now_state:
                continue
            if now_state == "failure":
                out.append(f"CI failed on {name}: {run.workflow} on {run.branch} ({run.title[:60]}).")
            elif now_state == "success" and before == "failure":
                out.append(f"CI is green again on {name}: {run.workflow} on {run.branch}.")
        return out
