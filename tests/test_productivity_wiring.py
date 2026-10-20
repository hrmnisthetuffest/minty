"""The router asks the productivity phrases after the butler's, and an idle voice pipeline is not touched by them."""
from minty_core.commands import CommandRouter


class Agent:
    history: list = []


def test_the_router_hands_unclaimed_text_to_the_productivity_phrases():
    seen = []

    def phrases(text):
        seen.append(text)
        return "Alarm set for 06:30." if "alarm" in text else None

    router = CommandRouter(Agent(), productivity=phrases)
    assert router.handle("set an alarm for 6:30") == "Alarm set for 06:30."
    assert router.handle("what is the capital of France") is None
    assert seen == ["set an alarm for 6:30", "what is the capital of France"]


def test_the_butler_goes_first():
    router = CommandRouter(Agent(), butler=lambda t: "butler" if "chore" in t else None,
                           productivity=lambda t: "productivity")
    assert router.handle("add a chore") == "butler"
    assert router.handle("anything else") == "productivity"
