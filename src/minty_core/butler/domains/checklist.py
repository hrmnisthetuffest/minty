"""Leaving-home checklist (937): read out when you ask, never on its own."""
from minty_core.butler.domain import Domain

LEAVING = Domain(
    name="leaving", label="leaving checklist", noun="item",
    aliases=("leaving checklist", "leaving-home checklist", "leave home checklist", "going out list"),
    persistent=True, remind="", seed=("keys", "wallet", "phone", "ID card", "charger", "water bottle"),
    patterns=(
        (r"^(?:i'?m |i am )?(?:leaving|heading out|going out)(?: now| home)?$", "list"),
        (r"^(?:what|read)(?:'s| is| me)?(?: on)? (?:the |my )?leaving(?:[- ]home)? checklist$", "list"),
    ),
)
