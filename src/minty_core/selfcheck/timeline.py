"""One list of what happened (678): jobs that ran and actions that were taken (or refused), newest first. Built from the job runner
and the action receipts; nothing new is recorded for it."""
from __future__ import annotations

MAX_TEXT = 160


def _cut(text, n: int = MAX_TEXT) -> str:
    return " ".join(str(text or "").split())[:n]


def build_timeline(jobs, receipts, limit: int = 80) -> list[dict]:
    items = []
    for j in jobs:
        when = j.finished or j.started or j.created
        running = j.status in ("queued", "running", "waiting_approval", "waiting")
        detail = (getattr(j, "progress", None) or [""])[-1] if running else j.result
        items.append({"id": f"job:{j.id}", "ts": float(when or 0), "kind": "job", "title": _cut(j.label() or j.goal),
                      "status": j.status, "detail": _cut(detail)})
    for r in receipts:
        items.append({"id": f"receipt:{r.id}", "ts": float(r.ts), "kind": "action", "title": _cut(r.summary),
                      "status": r.outcome, "detail": _cut(f"risk {r.risk}" + (f"; {r.detail}" if r.detail else ""))})
    items.sort(key=lambda i: -i["ts"])
    return items[:limit]
