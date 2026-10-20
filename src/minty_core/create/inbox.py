"""The idea inbox's weekly review (660): what came in, what was finished, what has been sitting for weeks, and a nudge to pick
one. Reads the same idea notes the developer helpers use."""
from __future__ import annotations

WEEK = 7 * 86400.0
STALE_DAYS = 21


def weekly_review(ideas, now: float) -> str:
    open_rows = ideas.list(status="open")
    done_rows = ideas.list(status="done")
    if not open_rows and not done_rows:
        return "Your idea inbox is empty. Say 'note an idea' whenever one turns up."
    new = [r for r in open_rows + done_rows if now - r["created"] <= WEEK]
    finished = [r for r in done_rows if r.get("closed") and now - r["closed"] <= WEEK]
    older = [r for r in open_rows if now - r["created"] > WEEK]
    lines = [f"This week: {len(new)} new this week, {len(finished)} finished."]
    if older:
        oldest = min(older, key=lambda r: r["created"])
        age = int((now - oldest["created"]) // 86400)
        stale = sum(1 for r in older if now - r["created"] > STALE_DAYS * 86400)
        lines.append(f"Still open from before: {len(older)} (the oldest: {oldest['text'][:80]}, {age} days"
                     + (f"; {stale} of them over {STALE_DAYS} days" if stale else "") + ").")
        lines.append("Pick one to start this week, or let one go. Say 'done' on any you have finished.")
    elif open_rows:
        lines.append("Everything open is from this week. Nothing is going stale.")
    return " ".join(lines)
