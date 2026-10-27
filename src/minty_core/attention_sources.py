"""Where open loops come from. Each function reads one part of Minty and lists what is waiting for the user."""
from __future__ import annotations

import time
from typing import Callable

from minty_core.attention import Loop

FAILED_KEEP_S = 3 * 86400  # a failed mission is offered for a retry for three days, then left alone
SCAN_DOMAINS = ("todo", "waiting", "competitions", "documents", "chores")  # the butler lists that have deadlines
URGENT_LATE_S = 3 * 86400


def mission_loops(jobs, clock: Callable[[], float] = time.time) -> list[Loop]:
    """Missions that are waiting for the user, were cut short, or failed lately (done ones and other jobs announce themselves)."""
    if jobs is None:
        return []
    now, out = clock(), []
    for job in jobs.select(kinds={"mission"}):
        since = job.finished or job.created
        if job.status == "blocked":
            out.append(Loop(f"mission:{job.id}:blocked", "mission",
                            f"Mission {job.id} needs you: {job.question or job.result} Say the answer and I will carry on.",
                            since, "now"))
        elif job.status == "interrupted" and job.resumable:
            out.append(Loop(f"mission:{job.id}:interrupted", "mission",
                            f"Mission {job.id} was interrupted when I restarted. Say resume mission {job.id} to carry on.",
                            since, "today"))
        elif job.status in ("failed", "timeout") and job.resumable and now - since < FAILED_KEEP_S:
            out.append(Loop(f"mission:{job.id}:failed", "mission",
                            f"Mission {job.id} did not finish: {job.result[:120]} Say resume mission {job.id} to try again.",
                            since, "later"))
    return out


def butler_loops(provider, clock: Callable[[], float] = time.time) -> list[Loop]:
    """Overdue items and follow-ups whose date has come, from the butler lists that carry deadlines."""
    if provider is None:
        return []
    try:
        butler = provider.butler()
    except Exception:  # noqa: BLE001 — switched off
        return []
    now, out = clock(), []
    for domain in SCAN_DOMAINS:
        if domain not in butler.domains:
            continue
        for item in butler.list(domain, "open"):
            if item.due is None or item.due > now:
                continue
            late = now - item.due
            text = f"Follow up: {item.name}" if domain == "waiting" else f"Overdue: {item.name}"
            out.append(Loop(f"butler:{domain}:{item.id}", domain, text, item.due, "today" if late < URGENT_LATE_S else "later"))
    return out
