"""Where a record may go: the label every health and finance row carries, and the one place that decides.

LOCAL_ONLY (Strava API data, by its terms): never in any prompt, not even as a summary; shown only to you.
SENSITIVE (Hevy, Cronometer, Health Connect, Strava archive): raw rows never leave; a small facts pack may reach a
provider that allows private turns.
FINANCIAL_LOCAL: the same, for money.
NORMAL: the ordinary rules.
An origin nobody listed is treated as SENSITIVE, so a new importer fails closed.
"""
from __future__ import annotations

NORMAL = "normal"
SENSITIVE = "personal_sensitive"
FINANCIAL_LOCAL = "financial_local"
LOCAL_ONLY = "local_only"

_ORIGINS = {
    "strava_api": LOCAL_ONLY,
    "strava_export": SENSITIVE, "hevy": SENSITIVE, "cronometer_csv": SENSITIVE, "health_connect": SENSITIVE,
    "manual": SENSITIVE, "chat": NORMAL, "notes": NORMAL,
}

# what a tool's result is replaced with in the conversation when the real text was for your eyes only
LOCAL_STUB = "[a local-only result was shown to the user; it is not kept]"


def residency_of(origin: str) -> str:
    origin = (origin or "").lower()
    if origin.startswith("finance"):
        return FINANCIAL_LOCAL
    return _ORIGINS.get(origin, SENSITIVE)


def may_enter_prompt(residency: str, *, as_facts: bool) -> bool:
    """Raw rows only if NORMAL; a derived facts pack also from SENSITIVE and FINANCIAL_LOCAL; never LOCAL_ONLY."""
    if residency == NORMAL:
        return True
    if residency in (SENSITIVE, FINANCIAL_LOCAL):
        return as_facts
    return False


class LocalResult(str):
    """A tool result that is for the user only: the agent shows it and keeps none of it."""
