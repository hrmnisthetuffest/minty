import pytest

from minty_core.claimcheck import correction, unbacked_claims
from minty_core.llm.types import TextDelta, ToolCall, ToolCallEvent
from minty_core.plugins.host import Tool
from tests.test_agent import make_agent, speak

OBJ = {"type": "object", "properties": {}}


@pytest.mark.parametrize("reply,kinds", [
    ("I sent the message to Sam.", ["sent"]),
    ("I've emailed her the file and I deleted the old copy.", ["sent", "deleted"]),
    ("Okay, I have installed it.", ["installed"]),
    ("The message has been sent.", ["sent"]),
    ("All set. I've removed those reminders.", ["deleted"]),
])
def test_claims_of_a_change_are_found(reply, kinds):
    assert sorted(unbacked_claims(reply, [])) == sorted(kinds)


@pytest.mark.parametrize("reply", [
    "Should I send it to Sam?", "I couldn't send it.", "I haven't sent anything yet.", "I can delete that if you like.",
    "I sent it earlier today.", "You sent that yesterday.", "It was already installed.", "The weather is sunny.",
    "I will send it now.", "Sent.", "If I sent it you'd get a receipt.",
])
def test_ordinary_sentences_are_not_claims(reply):
    assert unbacked_claims(reply, []) == []


def test_a_claim_backed_by_a_matching_change_passes_and_a_mismatch_does_not():
    assert unbacked_claims("I sent it.", ["mail send with to sam"]) == []
    assert unbacked_claims("I sent it.", ["files delete with path a"]) == ["sent"]
    assert unbacked_claims("I deleted it.", ["files delete with path a"]) == []
    assert unbacked_claims("I installed it.", ["installer install with name x"]) == []


def test_the_correction_names_what_did_not_happen():
    assert correction(["sent"]) == "Correction: I haven't actually sent anything; nothing was done."
    assert "sent or deleted" in correction(["sent", "deleted"])


async def test_a_false_claim_is_corrected_after_it_is_spoken(tmp_path):
    agent, _, _ = make_agent(tmp_path, [[TextDelta("I sent the message to Sam.")]])
    out = await speak(agent)
    assert out[0] == "I sent the message to Sam." and out[-1].startswith("Correction:")
    assert "Correction:" in agent.history[-1].content  # the next turn sees the correction too


async def test_a_true_claim_is_left_alone(tmp_path):
    send = Tool("mail_send", "send", OBJ, lambda a: "sent", risky=True)
    rounds = [[ToolCallEvent(ToolCall("c1", "mail_send", {"to": "sam"}))], [TextDelta("I sent the message to Sam.")]]
    agent, _, _ = make_agent(tmp_path, rounds, [send])
    assert await speak(agent) == ["I sent the message to Sam."]


async def test_a_send_that_failed_does_not_back_a_claim(tmp_path):
    send = Tool("mail_send", "send", OBJ, lambda a: "sent", risky=True)
    rounds = [[ToolCallEvent(ToolCall("c1", "mail_send", {"to": "sam"}))], [TextDelta("I sent the message to Sam.")]]
    agent, _, host = make_agent(tmp_path, rounds, [send])

    async def failing(name, args):
        return "Error: server said no"

    host.call = failing
    assert (await speak(agent))[-1].startswith("Correction:")
