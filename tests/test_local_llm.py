"""The optional local model: gated, text-only, off unless measured good (W1-29)."""
import json

import pytest

from minty_core.config import Config
from minty_core.llm.local import LocalModel, adopt, adopted, gate
from minty_core.llm.types import LLMError, Message, TextDelta, ToolSpec


class FakeLlama:
    def __init__(self, reply="Planning a science project"):
        self.reply, self.calls = reply, []

    def create_chat_completion(self, messages, max_tokens, temperature):
        self.calls.append(messages)
        return {"choices": [{"message": {"content": self.reply}}]}


def model(tmp_path, llama=None):
    path = tmp_path / "tiny.gguf"
    path.write_bytes(b"gguf")
    return LocalModel(str(path), loader=lambda p, n: llama or FakeLlama())


def test_the_gate_fails_closed():
    assert gate(6.0, 1800)[0] is True
    assert gate(2.5, 1800) == (False, "too slow here: 2.5 tokens/s, needs 4")
    assert gate(8.0, 3000)[0] is False and "memory" in gate(8.0, 3000)[1]
    assert gate(0.0, 0.0) == (False, "not measured")


async def test_it_answers_text_and_refuses_tools(tmp_path):
    m = model(tmp_path)
    events = [e async for e in m.stream([Message("user", "title this chat")], [])]
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Planning a science project"]
    with pytest.raises(LLMError):
        [e async for e in m.stream([Message("user", "x")], [ToolSpec("t", "d", {})])]


def test_without_a_file_or_the_library_it_says_why(tmp_path):
    ok, why = LocalModel("").available()
    assert not ok and "no model file" in why
    path = tmp_path / "m.gguf"
    path.write_bytes(b"x")
    ok, why = LocalModel(str(path)).available()  # default loader: llama_cpp is not installed in this environment
    assert (ok is False and "llama-cpp-python is not installed" in why) or ok is True


def test_adoption_is_recorded_and_a_broken_model_is_not_adopted(tmp_path):
    state = tmp_path / "local-llm.json"
    ticks = iter([0.0, 0.5])  # the clock inside measure(): 0.5 s for the answer
    import minty_core.llm.local as local
    real = local.measure
    local.measure = lambda m: (7.0, 1200.0)
    try:
        assert adopt(model(tmp_path), state)[0] is True and adopted(state) is True
    finally:
        local.measure = real

    class Broken:
        def create_chat_completion(self, **kw):
            raise RuntimeError("out of memory")

    verdict = adopt(model(tmp_path, Broken()), state)
    assert verdict == (False, "could not run: RuntimeError") and adopted(state) is False
    assert json.loads(state.read_text())["adopted"] is False
    assert next(ticks) == 0.0


def test_it_is_off_by_default():
    assert Config().local_llm.enabled is False


async def test_summaries_use_the_local_model_only_when_it_is_adopted(tmp_path):
    from minty_core.context import build_context
    ctx = build_context(tmp_path)
    path = tmp_path / "m.gguf"
    path.write_bytes(b"x")
    data = ctx.store.config.model_dump(mode="json")
    data["local_llm"].update(enabled=True, model_path=str(path))
    ctx.store.update(data)
    summarize = ctx.services["summarize"]
    # enabled but never measured: not adopted, so the providers (none here) and the plain extract answer
    out = await summarize("Summarise.", "The meet is on 10 December. Bring the spikes.", 80)
    assert "meet" in out
    import minty_core.context as context_module
    seen = []

    class Local:
        def __init__(self, *a, **k):
            pass

        async def stream(self, messages, tools):
            seen.append(messages[-1].content)
            yield TextDelta("A short local summary.")

    (tmp_path / "local-llm.json").write_text(json.dumps({"adopted": True}))
    original = context_module.LocalModel
    context_module.LocalModel = Local
    try:
        assert await summarize("Summarise.", "The meet is on 10 December.", 80) == "A short local summary."
    finally:
        context_module.LocalModel = original
    assert seen == ["The meet is on 10 December."]
