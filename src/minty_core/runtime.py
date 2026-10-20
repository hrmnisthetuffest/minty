"""Python runtime tuning done once, after start-up (docs/performance-plan.md, B4).

A long-running assistant allocates many small short-lived objects and keeps a large, steady set of long-lived ones (settings,
plugins, tables). The default collector rescans the steady set again and again. Freezing it after start-up moves it out of the
collector's sight, and a higher threshold makes the young-generation scan rarer; both cut pauses and CPU on slow machines. Nothing
is disabled: cycles are still collected.
"""
from __future__ import annotations

import gc

DEFAULT_THRESHOLD = 700  # what CPython ships with
TUNED_THRESHOLD = (50_000, 20, 20)


def tune_gc() -> dict:
    """Collect once, freeze everything alive, and raise the threshold unless someone already chose one."""
    if gc.get_threshold()[0] == DEFAULT_THRESHOLD:
        gc.set_threshold(*TUNED_THRESHOLD)
    gc.collect()
    gc.freeze()
    return {"threshold": gc.get_threshold(), "frozen": gc.get_freeze_count()}
