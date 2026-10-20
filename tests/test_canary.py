"""Daily provider canary (W1B-10)."""
import json
from types import SimpleNamespace

from minty_core.canary import PROMPTS, run_canary
from minty_core.llm.types import LLMError


class FakeRouter:
    def __init__(self, providers, answers, probe=None):
        self.providers, self.answers, self.asked = providers, answers, []
        self._probe = probe or {p.name: "ok" for p in providers}

    def usable(self):
        return self.providers

    async def probe(self):
        return self._probe

    async def ask_one(self, name, messages, timeout_s=20.0):
        self.asked.append(name)
        reply = self.answers[name]
        if isinstance(reply, Exception):
            raise reply
        return reply(messages[0].content), 0.4


def provider(name, paid=False):
    return SimpleNamespace(name=name, is_paid=lambda: paid)


def correct(question):
    return dict(PROMPTS_ANSWERS)[question]


PROMPTS_ANSWERS = [(q, a) for q, a in zip((p for p, _ in PROMPTS), ("OK", "4", "Paris", "blue", "tac"))]


async def test_each_free_provider_gets_five_prompts_and_a_score(tmp_path):
    router = FakeRouter([provider("groq"), provider("nvidia"), provider("anthropic", paid=True)],
                        {"groq": correct, "nvidia": lambda q: "I don't know", "anthropic": correct})
    report = await run_canary(router, share=lambda: 0.9, state_path=tmp_path / "canary.json")
    scores = {r.name: (r.passed, r.total) for r in report.results}
    assert scores == {"groq": (5, 5), "nvidia": (0, 5), "anthropic": (0, 0)}  # a paid provider is never asked
    assert "anthropic" not in router.asked and len(router.asked) == 10
    assert report.words() == "Provider check: groq 5 of 5; nvidia 0 of 5; anthropic key ok."
    assert json.loads((tmp_path / "canary.json").read_text())["results"][0]["name"] == "groq"


async def test_a_low_capacity_day_only_checks_keys():
    router = FakeRouter([provider("groq")], {"groq": correct})
    report = await run_canary(router, share=lambda: 0.2)
    assert router.asked == [] and report.skipped == "only 20 percent of today's capacity is left"
    assert report.words() == ("Provider check: groq key ok. I skipped the questions: only 20 percent of today's "
                              "capacity is left.")


async def test_a_bad_key_and_errors_are_reported():
    router = FakeRouter([provider("gemini"), provider("mistral")],
                        {"gemini": correct, "mistral": LLMError("boom", kind="timeout")},
                        probe={"gemini": "rejected the key (HTTP 401)", "mistral": "ok"})
    report = await run_canary(router, share=lambda: None)
    assert router.asked.count("gemini") == 0 and router.asked.count("mistral") == 5
    text = report.words()
    assert "gemini failed its key check (rejected the key (HTTP 401))" in text and "mistral 0 of 5 (timeout)" in text


async def test_the_real_router_asks_exactly_one_provider_without_failing_over(tmp_path):
    from tests.test_turn_deadline import Slow, router_with
    a, b = Slow("a", 0.0, text="from a"), Slow("b", 0.0, text="from b")
    router, called = router_with(tmp_path, {"a": a, "b": b}, deadline=2.0)
    from minty_core.llm.types import Message
    text, seconds = await router.ask_one("b", [Message("user", "hi")])
    assert text == "from b" and seconds >= 0 and called == ["b"]
    failing = Slow("c", 0.0, fail=LLMError("c: HTTP 503", status=503))
    router2, _ = router_with(tmp_path / "x", {"c": failing, "d": Slow("d", 0.0, text="fine")}, deadline=2.0)
    import pytest
    with pytest.raises(LLMError):
        await router2.ask_one("c", [Message("user", "hi")])  # no quiet fail-over to d


async def test_by_voice_it_starts_in_the_background():
    from minty_core.commands import CommandRouter
    started = []
    router = CommandRouter(agent=None, canary=lambda: started.append(1) or "Checking the providers now.")
    assert router.handle("check the models") == "Checking the providers now." and started == [1]
