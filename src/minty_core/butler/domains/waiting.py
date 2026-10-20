"""Waiting-for list (1053): things others owe you, with a gentle follow-up date."""
from minty_core.butler.domain import Domain

WAITING = Domain(
    name="waiting", label="waiting-for list", noun="follow-up", aliases=("waiting list", "waiting for", "follow-ups"),
    needs_due=True, default_when="in 3 days", fields=("who",), remind="Follow up: {name}.",
    patterns=(
        (r"^(?:i'?m|i am) waiting (?:for|on) (?P<who>[\w' ]+?) to (?P<name>.+?)"
         r"(?: (?P<when>(?:by|on|in|before|tomorrow|next|this).+))?$", "add"),
        (r"^what(?: am i|'s| is)? (?:i'?m )?waiting for$", "list"),
        (r"^(?P<who>[A-Z][\w']+) (?:replied|got back to me|sent (?:it|them|the .+)|came back)$", "done"),
    ),
)
