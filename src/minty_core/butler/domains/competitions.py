"""Competitions and deadlines (731): olympiads, contests, registrations. Early reminders at 14, 7, 2 and 1 days."""
from minty_core.butler.domain import Domain

COMPETITIONS = Domain(
    name="competitions", label="competitions and deadlines", noun="deadline",
    aliases=("competitions", "competition", "contests", "contest", "olympiads", "deadlines"),
    needs_due=True, lead_days=(14, 7, 2, 1), remind="Deadline today: {name}.",
    patterns=(
        (r"^(?P<name>.*\b(?:competition|olympiad|contest|hackathon)\b.*?) (?:registration |entries )?"
         r"(?:closes|deadline|is due|is on)(?: is)?(?: on| by)? (?P<when>.+)$", "add"),
    ),
)
