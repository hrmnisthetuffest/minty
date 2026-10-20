"""Chores rota (812): recurring chores, voice check-off, gentle reminders. The whole domain is this spec."""
from minty_core.butler.domain import Domain

CHORES = Domain(
    name="chores",
    label="chores rota",
    noun="chore",
    aliases=("chore", "rota", "chores list"),
    repeating=True,
    remind="Chore time: {name}.",
)
