"""'What I learned about you this week' (610): the new facts, numbered, so each can be kept, corrected or dropped."""
from __future__ import annotations

WEEK = 7 * 86400


def learned_this_week(audit, now: float, limit: int = 12) -> str:
    fresh = [e for e in audit.entries(kind="facts") if e.ts and e.ts >= now - WEEK and e.id.startswith("fact:")]
    if not fresh:
        return "I haven't learned anything new about you this week."
    n = len(fresh)
    lines = [f"This week I learned {n} thing{'s' * (n != 1)} about you:"]
    for i, e in enumerate(fresh[:limit], 1):
        lines.append(f"{i}) {e.text} [{e.id}, {e.date}]")
    lines.append('Tell me "forget" with an id to drop one, or "correct" with an id and the right words.')
    return "\n".join(lines)


def learned_notice(audit, now, last_day: str, enabled: bool) -> tuple[str | None, str]:
    """The Sunday-evening notice, once. Returns (text or None, the day to remember as 'already sent')."""
    day = now.strftime("%Y-%m-%d")
    if not enabled or now.weekday() != 6 or now.hour < 18 or last_day == day:
        return None, last_day
    text = learned_this_week(audit, now.timestamp())
    if text.startswith("I haven't"):
        return None, day  # nothing new: stay quiet, and do not look again today
    return text, day
