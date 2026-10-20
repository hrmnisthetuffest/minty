"""Pseudonymise before cloud (W0-23): providers that may train on prompts see placeholders, never the real
names, e-mails, phone numbers or addresses; the reply is mapped back locally, even when a placeholder is split
across streamed chunks."""
import pytest

from tests.test_router import Clock
from minty_core.config import ConfigStore
from minty_core.keystore import SecretsStore
from minty_core.llm.router import ProviderRouter
from minty_core.llm.types import Message, TextDelta, ToolCall, ToolCallEvent
from minty_core.pseudonym import Pseudonymizer, StreamUnmasker


def test_emails_phones_addresses_and_known_names_become_placeholders():
    p = Pseudonymizer(names=["Alex", "Mrs Kaur"])
    text = ("Email alex@example.com or call +91 98765 43210. Alex lives at 14 Rose Lane, Model Town. "
            "Ask Mrs Kaur, and again alex@example.com.")
    masked = p.mask(text)
    for secret in ("alex@example.com", "98765", "14 Rose Lane", "Alex", "Mrs Kaur"):
        assert secret not in masked
    assert masked.count("EMAIL_1") == 2 and "PHONE_1" in masked and "ADDRESS_1" in masked
    assert "PERSON_1" in masked and "PERSON_2" in masked
    assert p.unmask(masked) == text


def test_ordinary_numbers_and_words_are_left_alone():
    p = Pseudonymizer()
    text = "In 2026 the price was 4999 rupees for 3 items, order 12345."
    assert p.mask(text) == text


def test_messages_and_tool_arguments_are_masked_on_a_copy():
    p = Pseudonymizer(names=["Alex"])
    msgs = [Message("user", "mail alex@example.com for Alex"),
            Message("assistant", "", tool_calls=[ToolCall("1", "mail_send", {"to": "alex@example.com"})])]
    masked = p.mask_messages(msgs)
    assert masked[0].content == "mail EMAIL_1 for PERSON_1" and masked[1].tool_calls[0].arguments == {"to": "EMAIL_1"}
    assert msgs[0].content == "mail alex@example.com for Alex"  # the history is untouched


@pytest.mark.parametrize("chunks", [
    ["Hello PERSON_1, your mail EMAIL_1 is set."],
    ["Hello PER", "SON_1, your mail EM", "AIL_1 is set."],
    ["Hello P", "E", "R", "S", "O", "N", "_", "1", ", your mail EMAIL_", "1", " is set."],
    ["Hello PERSON_1", ", your mail EMAIL_1", " is set."],
])
def test_streamed_reply_is_mapped_back_even_when_a_placeholder_is_split(chunks):
    p = Pseudonymizer(names=["Alex"])
    p.mask("Alex alex@example.com")
    out = StreamUnmasker(p)
    text = "".join(out.feed(c) for c in chunks) + out.flush()
    assert text == "Hello Alex, your mail alex@example.com is set."


def test_capitals_that_are_not_placeholders_pass_through():
    p = Pseudonymizer()
    out = StreamUnmasker(p)
    text = "".join(out.feed(c) for c in ["NASA and the ", "USB_C port"]) + out.flush()
    assert text == "NASA and the USB_C port"


class Echo:
    """A provider that repeats what it was told, so the test can see exactly what left the machine."""

    def __init__(self, name, seen):
        self.name, self.seen = name, seen

    async def stream(self, messages, tools):
        self.seen.append(messages[-1].content)
        words = messages[-1].content.split()
        for i in range(0, len(messages[-1].content), 3):
            yield TextDelta(messages[-1].content[i:i + 3])
        email = next((w for w in words if w.startswith("EMAIL_") or "@" in w), None)
        if email:
            yield ToolCallEvent(ToolCall("1", "mail_draft", {"to": email}))


def router_for(tmp_path, provider: dict, **router_cfg):
    store = ConfigStore(tmp_path)
    data = store.load().model_dump(mode="json")
    data["providers"] = [provider]
    data["router"].update(router_cfg)
    data["safety"]["pseudonym_names"] = ["Alex"]
    store.update(data)
    secrets = SecretsStore(tmp_path)
    secrets.set(provider["name"], "key-x")
    seen = []
    return ProviderRouter(store, secrets, lambda cfg, key: Echo(cfg.name, seen), Clock()), seen


GEMINI = {"name": "gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "g"}
GROQ = {"name": "groq", "base_url": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b"}
REQUEST = "draft a note to alex@example.com from Alex"


async def run(router):
    events = [e async for e in router.stream([Message("user", REQUEST)], [])]
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    calls = [e.call for e in events if isinstance(e, ToolCallEvent)]
    return text, calls


async def test_a_provider_that_trains_on_prompts_only_sees_placeholders(tmp_path):
    router, seen = router_for(tmp_path, GEMINI)
    text, calls = await run(router)
    assert "alex" not in seen[0].lower() and "EMAIL_1" in seen[0] and "PERSON_1" in seen[0]
    assert text == REQUEST and calls[0].arguments == {"to": "alex@example.com"}  # mapped back locally


async def test_a_provider_that_does_not_train_sees_the_request_as_is(tmp_path):
    router, seen = router_for(tmp_path, GROQ)
    await run(router)
    assert seen == [REQUEST]


async def test_pseudonymising_can_be_switched_off(tmp_path):
    router, seen = router_for(tmp_path, GEMINI, pseudonymize=False)
    await run(router)
    assert seen == [REQUEST]
