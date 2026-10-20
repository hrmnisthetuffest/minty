"""Documents with renewals (822): what it is, when it runs out, where it is kept. Never the number on it."""
from minty_core.butler.domain import Domain

DOCUMENTS = Domain(
    name="documents", label="documents list", noun="document", aliases=("documents", "document", "papers", "renewals"),
    needs_due=True, lead_days=(30, 7, 1), fields=("where",), remind="Renewal due today: {name}.",
    patterns=(
        (r"^my (?P<name>.+?) (?:expires|runs out|is valid (?:till|until)|is due for renewal)"
         r"(?: (?:on|in|by))? (?P<when>.+)$", "add"),
    ),
)
