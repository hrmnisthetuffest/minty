"""Claude through the user's own Claude Code login (`claude -p`): tested against a fake `claude` program that records
how it was started, plus (opt-in, MINTY_LIVE_CLAUDE=1) the real one."""
import json
import os
import shutil
import stat
import sys
import textwrap
import time

import pytest

from minty_core.config import ConfigStore, ProviderConfig, claude_brain_providers
from minty_core.keystore import SecretsStore
from minty_core.llm.claude_cli import ClaudeCliProvider, claude_command
from minty_core.llm.router import ProviderRouter
from minty_core.llm.types import LLMError, Message, TextDelta, ToolCall, ToolCallEvent, ToolNotOffered, ToolSpec

FAKE = '''#!{python}
import json, os, sys, time
args = sys.argv[1:]
prompt = sys.stdin.read()
sysfile = args[args.index("--system-prompt-file") + 1]
log = {{"args": args, "env": dict(os.environ), "cwd": os.getcwd(), "stdin": prompt,
        "system": open(sysfile).read(), "system_file": sysfile}}
open(__file__ + ".log", "a").write(json.dumps(log) + "\\n")

def out(**d):
    print(json.dumps(d), flush=True)

def delta(text):
    out(type="stream_event", event={{"type": "content_block_delta", "delta": {{"type": "text_delta", "text": text}}}})

def window(status="allowed", five=0.1):
    out(type="rate_limit_event", rate_limit_info={{"status": status, "resetsAt": time.time() + 1200, "rateLimitType": "five_hour",
        "unifiedWindows": {{"five_hour": {{"utilization": five, "resetsAt": time.time() + 1200}}}}}})

def result(text, error=False):
    out(type="result", subtype="success", is_error=error, result=text,
        usage={{"input_tokens": 3, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 5, "output_tokens": 7}})

if "MODE:slow" in prompt:
    time.sleep(30)
elif "MODE:limit" in prompt:
    window("rejected", 1.0)
    result("You've hit your limit - resets 3pm", error=True)
elif "MODE:login" in prompt:
    result("Invalid API key - Please run /login", error=True)
elif "MODE:warm" in prompt:
    window("allowed", 0.95)
    delta("Fine.")
    result("Fine.")
elif "MODE:tool" in prompt:
    window()
    delta("Checking. <<")
    delta("CALL>> " + json.dumps({{"name": "weather_get_weather", "arguments": {{"city": "Paris"}}}}))
    result("Checking. <<CALL>> ...")
elif "MODE:ghost" in prompt:
    window()
    delta('<<CALL>> {{"name": "browser_open", "arguments": {{}}}}')
    result("x")
elif "MODE:broken" in prompt:
    window()
    delta("<<CALL>> not json")
    result("x")
else:
    window()
    delta("Hello there")
    delta(", friend.")
    result("Hello there, friend.")
'''


@pytest.fixture(autouse=True)
def fresh_usage_windows():
    ClaudeCliProvider._WINDOWS.clear()  # what the account last reported is shared between calls, so reset it per test
    yield
    ClaudeCliProvider._WINDOWS.clear()


@pytest.fixture
def fake(tmp_path):
    path = tmp_path / "claude"
    path.write_text(FAKE.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def runs():
        log = tmp_path / "claude.log"
        return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []

    return path, runs


def provider(fake, tmp_path, **extra):
    cfg = ProviderConfig(name="claude", kind="claude_cli", model="sonnet", requires_key=False,
                         extra={"command": str(fake[0]), "effort": "low", **extra}, timeout_s=20)
    return ClaudeCliProvider(cfg, workdir=tmp_path / "work")


async def run(p, text, tools=(), system="You are Minty."):
    return [e async for e in p.stream([Message("system", system), Message("user", text)], list(tools))]


WEATHER = ToolSpec("weather_get_weather", "Weather for a city.", {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]})


async def test_text_streams_and_usage_is_recorded(fake, tmp_path):
    p = provider(fake, tmp_path)
    events = await run(p, "hi")
    assert "".join(e.text for e in events) == "Hello there, friend." and all(isinstance(e, TextDelta) for e in events)
    assert p.last_usage.tokens_out == 7 and p.last_usage.tokens_in == 18 and p.last_usage.cached == 10


async def test_it_can_only_write_text_and_never_leaks_the_prompt_or_a_key(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-never-be-passed")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-also-not")
    p = provider(fake, tmp_path)
    await run(p, "my secret is hunter2", system="PROFILE: lives in Paris")
    (r,) = fake[1]()
    a = r["args"]
    assert a[a.index("--tools") + 1] == "" and a[a.index("--setting-sources") + 1] == ""
    assert {"--strict-mcp-config", "--no-session-persistence", "-p"} <= set(a)
    assert a[a.index("--model") + 1] == "sonnet" and a[a.index("--effort") + 1] == "low"
    assert "hunter2" not in " ".join(a) and "Paris" not in " ".join(a)  # ps would show them
    assert "hunter2" in r["stdin"] and "Paris" in r["system"]
    assert not any(k.startswith("ANTHROPIC") or k.startswith("GROQ") for k in r["env"])
    assert r["cwd"] == str(tmp_path / "work") and not os.path.exists(r["system_file"])


async def test_a_tool_call_is_parsed_even_when_the_marker_is_split_across_chunks(fake, tmp_path):
    events = await run(provider(fake, tmp_path), "MODE:tool", [WEATHER])
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Checking. "  # the marker never reaches the speaker
    calls = [e.call for e in events if isinstance(e, ToolCallEvent)]
    assert len(calls) == 1 and calls[0].name == "weather_get_weather" and calls[0].arguments == {"city": "Paris"}
    system = fake[1]()[0]["system"]
    assert "<<CALL>>" in system and "weather_get_weather(city: string)" in system


async def test_a_tool_that_was_not_offered_is_refused_so_the_agent_can_load_its_group(fake, tmp_path):
    with pytest.raises(ToolNotOffered) as e:
        await run(provider(fake, tmp_path), "MODE:ghost", [WEATHER])
    assert e.value.tool == "browser_open"


async def test_a_malformed_tool_call_is_an_error_not_a_guess(fake, tmp_path):
    with pytest.raises(LLMError) as e:
        await run(provider(fake, tmp_path), "MODE:broken", [WEATHER])
    assert "malformed" in str(e.value)


async def test_a_used_up_account_is_a_rate_limit_with_the_reset_time(fake, tmp_path):
    with pytest.raises(LLMError) as e:
        await run(provider(fake, tmp_path), "MODE:limit")
    assert e.value.kind == "rate_limit" and 60 <= e.value.retry_after <= 3600


async def test_it_keeps_the_last_of_the_window_for_your_own_use(fake, tmp_path):
    p = provider(fake, tmp_path)
    await run(p, "MODE:warm")  # answers, but reports 95 % of the five-hour window used
    assert p.window["five_hour"] == 0.95
    with pytest.raises(LLMError) as e:
        await run(p, "hi again")
    assert e.value.kind == "rate_limit" and "keeping the rest for you" in str(e.value)
    assert len(fake[1]()) == 1  # the second call never started the program


async def test_not_signed_in_is_an_auth_error(fake, tmp_path):
    with pytest.raises(LLMError) as e:
        await run(provider(fake, tmp_path), "MODE:login")
    assert e.value.kind == "auth" and e.value.status == 401


async def test_a_slow_answer_times_out_and_the_program_is_stopped(fake, tmp_path):
    cfg = ProviderConfig(name="claude", kind="claude_cli", model="haiku", requires_key=False,
                         extra={"command": str(fake[0])}, timeout_s=0.6)
    started = time.monotonic()
    with pytest.raises(LLMError) as e:
        await run(ClaudeCliProvider(cfg, workdir=tmp_path / "w"), "MODE:slow")
    assert e.value.kind == "timeout" and time.monotonic() - started < 5


async def test_a_missing_program_is_a_clear_error(tmp_path):
    cfg = ProviderConfig(name="claude", kind="claude_cli", model="haiku", requires_key=False,
                         extra={"command": str(tmp_path / "nope")})
    with pytest.raises(LLMError) as e:
        await run(ClaudeCliProvider(cfg, workdir=tmp_path / "w"), "hi")
    assert "not installed" in str(e.value)


# -- how Minty uses it ----------------------------------------------------------------------------------------

def router_with(tmp_path, fake, others=()):
    store = ConfigStore(tmp_path / "cfg")
    store.load()
    claude = [c.model_copy(update={"extra": {**c.extra, "command": str(fake[0])}}) for c in claude_brain_providers()]
    data = store.config.model_dump(mode="json")
    data["providers"] = [c.model_dump(mode="json") for c in claude] + [
        {"name": "free", "model": "m", "requires_key": False, "base_url": "http://x"} for _ in others]
    data["preferred_provider"] = "claude"
    store.update(data)
    seen = []

    def factory(cfg, key):
        if cfg.kind == "claude_cli":
            return ClaudeCliProvider(cfg, workdir=tmp_path / "w")
        seen.append(cfg.name)

        class Free:
            name = cfg.name

            async def stream(self, messages, tools):
                yield TextDelta("from the free provider")

        return Free()

    return ProviderRouter(store, SecretsStore(tmp_path / "cfg"), factory), seen


async def models_used(router, text, tier="fast"):
    return "".join([e.text async for e in router.stream([Message("system", "s"), Message("user", text)], [], tier=tier)
                    if isinstance(e, TextDelta)])


def used(fake):
    out = []
    for r in fake[1]():
        a = r["args"]
        out.append((a[a.index("--model") + 1], a[a.index("--effort") + 1] if "--effort" in a else None))
    return out


async def test_the_ladder_haiku_low_haiku_high_sonnet_low_sonnet_high_for_thinking(fake, tmp_path):
    router, _ = router_with(tmp_path, fake)
    await models_used(router, "what time is it")  # simple: most turns
    await models_used(router, "explain why the sky is blue")  # medium
    await models_used(router, "plan a three day trip to Shimla and compare the hotels")  # hard
    await models_used(router, "prove that the square root of two is irrational", tier="deep")  # thinking mode
    assert used(fake) == [("haiku", "low"), ("haiku", "high"), ("sonnet", "low"), ("sonnet", "high")]


async def test_opus_only_when_you_say_so_at_the_effort_you_say(fake, tmp_path):
    router, _ = router_with(tmp_path, fake)
    router.claude_pick = ("opus", "max")
    await models_used(router, "what time is it")
    router.claude_pick = ("opus", None)  # no effort named: the program's own default
    await models_used(router, "what time is it")
    router.claude_pick = None
    await models_used(router, "what time is it")
    assert used(fake) == [("opus", "max"), ("opus", None), ("haiku", "low")]


async def test_the_usage_reserve_holds_across_calls_through_the_router(fake, tmp_path):
    router, _ = router_with(tmp_path, fake, others=("free",))
    assert await models_used(router, "MODE:warm hello") == "Fine."  # claude answers, window at 95 %
    assert await models_used(router, "hello again") == "from the free provider"
    assert len(fake[1]()) == 1  # the second call never started claude


async def test_when_claude_runs_out_the_free_providers_take_over_and_claude_rests(fake, tmp_path):
    router, seen = router_with(tmp_path, fake, others=("free",))
    assert await models_used(router, "MODE:limit hello") == "from the free provider"
    assert await models_used(router, "hello again") == "from the free provider"
    assert len(fake[1]()) == 1  # claude is resting; it is not started again on every turn
    assert [c["name"] for c in router.board() if c["state"] == "resting"] == ["claude"]


async def test_no_claude_program_means_no_claude_provider(tmp_path):
    store = ConfigStore(tmp_path)
    store.load()
    gone = [c.model_copy(update={"extra": {"command": str(tmp_path / "missing")}}) for c in claude_brain_providers()]
    data = store.config.model_dump(mode="json")
    data["providers"] = [c.model_dump(mode="json") for c in gone]
    store.update(data)
    assert ProviderRouter(store, SecretsStore(tmp_path)).usable() == []


def test_the_defaults_are_private_by_default_and_need_no_key():
    opus, claude = claude_brain_providers()  # (the thinking-mode provider, the everyday one)
    assert (claude.model, claude.tier) == ("haiku", "fast") and claude.extra["effort"] == "low"
    assert claude.extra["plan"] == {"small": ["haiku", "low"], "medium": ["haiku", "high"], "large": ["sonnet", "low"]}
    assert (opus.name, opus.model, opus.tier, opus.extra["effort"]) == ("claude-deep", "sonnet", "deep", "high")
    for c in (opus, claude):
        assert c.kind == "claude_cli" and not c.requires_key and not c.is_paid()
        assert c.effective_policy()["allow_sensitive"] is False and c.effective_policy()["trains_on_data"] is True


@pytest.mark.skipif(not os.environ.get("MINTY_LIVE_CLAUDE") or not shutil.which("claude"),
                    reason="uses your real Claude Code login: set MINTY_LIVE_CLAUDE=1")
async def test_the_real_claude_answers_in_text_and_calls_a_tool(tmp_path):
    cfg = ProviderConfig(name="claude", kind="claude_cli", model="haiku", requires_key=False, extra={"effort": "low"})
    p = ClaudeCliProvider(cfg, workdir=tmp_path / "w")
    text = "".join(e.text for e in await run(p, "Reply with exactly the word pong.") if isinstance(e, TextDelta))
    assert "pong" in text.lower()
    events = await run(p, "What is the weather in Paris right now? Use a tool.", [WEATHER])
    assert [e.call.name for e in events if isinstance(e, ToolCallEvent)] == ["weather_get_weather"]


# -- minty-core claude-brain on | off | status ----------------------------------------------------------------

def test_claude_brain_on_puts_claude_first_and_off_takes_it_away(tmp_path):
    from minty_core.claude_brain import claude_brain

    store = ConfigStore(tmp_path)
    store.load()
    before = [c.name for c in store.config.providers]
    msg = claude_brain("on", store, installed=True)
    assert "on" in msg and [c.name for c in store.config.providers][:2] == ["claude-deep", "claude"]
    assert store.config.preferred_provider == "claude" and [c.name for c in store.config.providers][2:] == before
    assert store.config.turn.first_token_deadline_s >= 12.0  # a Claude call takes 2 to 5 s, sometimes 10
    claude_brain("on", store, installed=True)  # twice changes nothing
    assert [c.name for c in store.config.providers].count("claude") == 1
    fresh = ConfigStore(tmp_path)
    fresh.load()
    assert fresh.config.preferred_provider == "claude" and fresh.config.providers[1].extra["plan"]["large"] == ["sonnet", "low"]  # saved
    claude_brain("off", store, installed=True)
    assert [c.name for c in store.config.providers] == before and store.config.preferred_provider == ""


def test_claude_brain_on_refuses_when_the_program_is_missing(tmp_path):
    from minty_core.claude_brain import claude_brain

    store = ConfigStore(tmp_path)
    store.load()
    msg = claude_brain("on", store, installed=False)
    assert "not installed" in msg and [c.name for c in store.config.providers if c.kind == "claude_cli"] == []


def test_the_command_exists():
    from minty_core.app import parse_args

    assert parse_args(["claude-brain", "status"]).action == "status"


def test_a_config_from_the_first_version_is_cleaned_up(tmp_path):
    from minty_core.claude_brain import claude_brain

    store = ConfigStore(tmp_path)
    store.load()
    old = store.config.model_dump(mode="json")
    old["providers"] = [{"name": "claude-opus", "kind": "claude_cli", "model": "opus", "tier": "deep",
                         "requires_key": False}] + old["providers"]
    store.update(old)
    claude_brain("on", store, installed=True)
    names = [c.name for c in store.config.providers]
    assert "claude-opus" not in names and names[:2] == ["claude-deep", "claude"]
    claude_brain("off", store, installed=True)
    assert not [n for n in [c.name for c in store.config.providers] if n.startswith("claude")]
