"""The terminal through the real application: safety layer, approval guard and plugin host, not fakes."""
import pytest

from minty_core.context import build_context
from minty_core.llm.types import ToolCall


def app(tmp_path, enabled=True, **safety):
    ctx = build_context(tmp_path / "cfg")
    data = ctx.store.config.model_dump(mode="json")
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    data["shell"].update({"enabled": enabled, "workdir": str(work)})
    data["safety"].update(safety)
    ctx.store.update(data)
    ctx.asked = []

    async def confirm(prompt):
        ctx.asked.append(prompt)
        return ctx.answer

    ctx.answer = True
    ctx.agent.confirm = confirm
    return ctx, work


async def run(ctx, command, n=1):
    return await ctx.agent._run_call(ToolCall(f"c{n}", "shell_run", {"command": command}))


async def test_switched_off_by_default_even_for_a_plain_read(tmp_path):
    ctx, _ = app(tmp_path, enabled=False)
    text, _ = await run(ctx, "ls")
    assert text.startswith("Blocked by a safety check") and "switched off" in text and ctx.asked == []


async def test_a_plain_read_runs_without_a_question(tmp_path):
    ctx, work = app(tmp_path)
    (work / "marker.txt").write_text("x")
    text, private = await run(ctx, "ls")
    assert "marker.txt" in text and ctx.asked == [] and private is True


async def test_sudo_is_refused_before_anyone_is_asked(tmp_path):
    ctx, _ = app(tmp_path)
    text, _ = await run(ctx, "sudo touch /tmp/never")
    assert "another user" in text and ctx.asked == []


async def test_any_other_command_is_asked_with_its_exact_text_and_a_no_runs_nothing(tmp_path):
    ctx, work = app(tmp_path)
    ctx.answer = False
    text, _ = await run(ctx, "touch declined.txt")
    assert text == "The user declined." and not (work / "declined.txt").exists()
    assert len(ctx.asked) == 1 and "touch declined.txt" in ctx.asked[0]


async def test_a_yes_runs_it_and_it_counts_as_a_change_this_turn(tmp_path):
    ctx, work = app(tmp_path)
    await run(ctx, "touch made.txt")
    assert (work / "made.txt").exists() and len(ctx.asked) == 1
    assert any(entry.startswith("shell run") for entry in ctx.agent._changed)  # a spoken "I made the file" is backed


@pytest.mark.parametrize("safety", [{"confirm_risky": False}, {"mode": "trusted"}])
async def test_not_even_never_ask_or_a_trusted_level_lets_a_command_through_silently(tmp_path, safety):
    ctx, work = app(tmp_path, **safety)
    ctx.answer = False
    await run(ctx, "touch silent.txt")
    assert len(ctx.asked) == 1 and not (work / "silent.txt").exists()


async def test_a_rule_that_says_allow_cannot_silence_the_question(tmp_path):
    ctx, work = app(tmp_path, rules=[{"match": "shell_*", "decision": "allow"}])
    ctx.answer = False
    await run(ctx, "touch ruled.txt")
    assert len(ctx.asked) == 1 and not (work / "ruled.txt").exists()


async def test_a_rule_that_says_deny_is_obeyed(tmp_path):
    ctx, work = app(tmp_path, rules=[{"match": "shell_*", "decision": "deny"}])
    text, _ = await run(ctx, "ls")
    assert ctx.asked == [] and ("Blocked" in text or "declined" in text)


async def test_lockdown_stops_even_a_plain_read(tmp_path):
    ctx, _ = app(tmp_path)
    ctx.agent.safety.lockdown = type("L", (), {"active": True})()
    text, _ = await run(ctx, "ls")
    assert text.startswith("Blocked by a safety check") and "lockdown" in text and ctx.asked == []
