"""The daily "what I did for you" list (1066), from the action receipts: what, when, why, and what could be undone."""
from __future__ import annotations

from datetime import datetime


def render(receipts, day_word: str = "today", undo: list[dict] | None = None, limit: int = 15) -> str:
    done = [r for r in receipts if r.outcome == "ok"]
    declined = sum(1 for r in receipts if r.outcome == "declined")
    blocked = sum(1 for r in receipts if r.outcome == "blocked")
    failed = sum(1 for r in receipts if r.outcome == "failed")
    if not (done or declined or blocked or failed):
        return f"I haven't done anything for you {day_word}."
    lines = [f"What I did for you {day_word} ({len(done)} change{'s' * (len(done) != 1)}):"]
    for r in done[-limit:]:
        at = datetime.fromtimestamp(r.ts).strftime("%H:%M")
        why = f" — you asked: {r.request[:70]}" if r.request else ""
        standing = f" ({r.detail})" if r.detail.startswith("standing approval") else ""
        lines.append(f"- {at} {r.summary}{standing}{why}")
    if len(done) > limit:
        lines.append(f"  (and {len(done) - limit} earlier ones)")
    stopped = [f"{n} {word}" for n, word in ((declined, "declined"), (blocked, "blocked"), (failed, "failed")) if n]
    if stopped:
        lines.append("Not done: " + ", ".join(stopped) + ".")
    if undo:
        lines.append("You can undo: " + "; ".join(f'say "undo {e["id"]}" for {e["label"]}' for e in undo[:5]) + ".")
    return "\n".join(lines)
