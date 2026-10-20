"""`router.fast_reasoning`: simple spoken turns can ask reasoning models for low effort, which is faster to the first word."""
from tests.test_frugal import GROQ, OTHER, ask, make


def with_setting(tmp_path, providers, value, level="F0"):
    router, log = make(tmp_path, providers, level=level)
    data = router._store.config.model_dump(mode="json")
    data["router"]["fast_reasoning"] = value
    router._store.update(data)
    return router, log


async def test_it_is_off_by_default(tmp_path):
    router, log = make(tmp_path, [GROQ])
    await ask(router, "what is the capital of france")
    assert "reasoning_effort" not in log[0][2] and router._store.config.router.fast_reasoning == ""


async def test_a_simple_turn_gets_low_effort_and_no_reasoning_text(tmp_path):
    router, log = with_setting(tmp_path, [GROQ], "low")
    await ask(router, "what is the capital of france")
    _, model, extra = log[0]
    assert model == "openai/gpt-oss-20b" and extra["reasoning_effort"] == "low" and extra["include_reasoning"] is False
    assert "max_completion_tokens" not in extra  # the length of the answer is left alone


async def test_a_hard_turn_keeps_its_full_effort(tmp_path):
    router, log = with_setting(tmp_path, [GROQ], "low")
    await ask(router, "plan my revision week for the maths exam and compare the two options")
    assert "reasoning_effort" not in log[0][2]


async def test_thinking_mode_is_never_cut(tmp_path):
    router, log = with_setting(tmp_path, [GROQ], "low")
    await ask(router, "what is the capital of france", tier="deep")
    assert "reasoning_effort" not in log[0][2]


async def test_providers_that_do_not_take_the_field_get_nothing(tmp_path):
    router, log = with_setting(tmp_path, [OTHER], "low")
    await ask(router, "hi")
    assert log[0][2] == {}


async def test_frugal_mode_keeps_its_own_rules(tmp_path):
    router, log = with_setting(tmp_path, [GROQ], "medium", level="F1")
    await ask(router, "hi")
    assert log[0][2]["reasoning_effort"] == "low" and log[0][2]["max_completion_tokens"] == 600
