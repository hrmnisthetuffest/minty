"""Personal facts the user mentions are saved on their own and found again later (S-4)."""
from minty_core.config import MemoryConfig
from minty_core.context import build_context
from minty_core.prompts import FACT_RULES


def build(tmp_path, **memory):
    ctx = build_context(tmp_path)
    data = ctx.store.config.model_dump(mode="json")
    data["memory"].update(memory)
    ctx.store.update(data)
    return ctx


def prompt_for(ctx, text):
    return ctx.agent._system_prompt(text)


async def test_a_remembered_fact_is_found_again_when_it_is_relevant_and_not_otherwise(tmp_path):
    ctx = build(tmp_path)
    assert await ctx.host.call("memory_remember", {"note": "Shoe size is UK 9 in running shoes."}) == "Noted."
    asked = prompt_for(ctx, "which shoe size should I order")
    unrelated = prompt_for(ctx, "what is the capital of France")
    assert "Shoe size is UK 9" in asked and "Shoe size" not in unrelated


async def test_facts_go_to_the_search_index_so_every_request_does_not_grow(tmp_path):
    ctx = build(tmp_path)
    before = len(prompt_for(ctx, "what is the capital of France"))
    for i in range(40):
        await ctx.host.call("memory_remember", {"note": f"Fact number {i}: favourite colour number {i} is shade {i}."})
    after = len(prompt_for(ctx, "what is the capital of France"))
    assert after - before < 250  # a one-line map, not forty facts
    assert not (tmp_path / "memory.md").exists() or "Fact number" not in (tmp_path / "memory.md").read_text()


async def test_the_same_fact_is_not_saved_twice(tmp_path):
    ctx = build(tmp_path)
    await ctx.host.call("memory_remember", {"note": "Dog Murphy turns 9 on 26 October."})
    await ctx.host.call("memory_remember", {"note": "Dog Murphy turns 9 on 26 October."})
    assert ctx.services["gists"].count() == 1


async def test_secrets_are_never_saved(tmp_path):
    ctx = build(tmp_path)
    for note in ("My password is 010101", "wifi PIN 4821", "my api key is gsk_abcdefghijklmnopqrstuvwxyz0123456789",
                 "OTP 552341 for the bank", "The vault passphrase is blue horse"):
        out = await ctx.host.call("memory_remember", {"note": note})
        assert "won't keep" in out, note
    assert ctx.services["gists"].count() == 0


async def test_the_rule_is_in_the_prompt_and_can_be_switched_off(tmp_path):
    assert MemoryConfig().auto_remember is True
    on = prompt_for(build(tmp_path / "a"), "hello")
    assert FACT_RULES.strip() in on and "memory_remember" in FACT_RULES
    off = prompt_for(build(tmp_path / "b", auto_remember=False), "hello")
    assert FACT_RULES.strip() not in off


async def test_without_the_search_index_notes_still_go_to_the_notes_file(tmp_path):
    ctx = build(tmp_path, gist_index=False)
    await ctx.host.call("memory_remember", {"note": "Likes grilled cheese in the morning."})
    assert "grilled cheese" in (tmp_path / "memory.md").read_text()
    assert ctx.services["gists"].count() == 0


def test_the_rule_is_short_because_it_is_sent_on_every_request():
    assert len(FACT_RULES) < 260
