"""'Where did I put it?' (938): say where you left something, ask later."""
from minty_core.butler.domain import Domain

PLACE = Domain(
    name="place", label="places list", noun="thing", aliases=("places", "where things are"), unique_names=True,
    fields=("where",), remind="",
    patterns=(
        (r"^(?:i )?(?:put|left|kept|parked|placed) (?:my |the )(?P<name>.+?) "
         r"(?P<where>(?:in|on|under|at|inside|behind|next to|near|beside) .+)$", "add"),
        (r"^where (?:is|are|did i (?:put|leave|keep)) (?:my |the )?(?P<name>.+)$", "find"),
    ),
)
