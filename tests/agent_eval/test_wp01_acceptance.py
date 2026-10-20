"""WP-01 acceptance (W0-16): the 2026-10-11 failures replayed against the real context, agent, router and browser
plugin, with a simulated model, simulated free-tier providers and a fake Chrome.

Time is scaled 1:10 so the suite stays fast: the 6 s first-token deadline is 0.6 s here and the 5 s p90 target is
0.5 s.
"""
import asyncio
import json
import random
import re
import time
from pathlib import Path

import pytest

from tests.fakes import FakeAudioIn, FakeEndpointer, FakePlayer, FakeSTT, FakeTTS, FakeWake
from tests.test_pipeline import wait_until
from minty_core.context import build_context
from minty_core.events import EventBus
from minty_core.llm.types import LLMError, TextDelta, ToolCall, ToolCallEvent, llm_error
from minty_core.pipeline import BRAIN_DOWN, MODELS_SLOW, STILL_WORKING, VoicePipeline
from minty_core.trace import read_events, turn_scope

REPLAY = json.loads((Path(__file__).parents[1] / "fixtures" / "replay_2026_10_11.json").read_text())["turns"]
SCALE = 0.1  # 1 s of real time is 0.1 s here
DEADLINE = 6.0 * SCALE
P90_TARGET = 5.0 * SCALE

# ---- a fake Chrome: pages by address, navigation by the label clicked --------------------------------------
PAGES = {
    "https://www.google.com": ("Google", ['searchbox "Search"', 'button "Google Search"', 'button "I\'m Feeling Lucky"',
                                          'link "Store"', 'link "Gmail"']),
    "https://store.google.com": ("Google Store", ['link "Phones"', 'link "Laptops"', 'link "Help"',
                                                  'searchbox "Search store"']),
    "https://store.google.com/phones": ("Phones - Google Store", ['link "Pixel 11"', 'link "Store"',
                                                                  'searchbox "Search"']),
    "https://www.google.com/search?q=cats": ("cats - Google Search", ['link "Result: Cats - Wikipedia"',
                                                                      'link "Result: Cat - Britannica"',
                                                                      'searchbox "Search"', 'link "Store"']),
    "https://en.wikipedia.org/wiki/Cat": ("Cat - Wikipedia", ['link "Main page"', 'link "Store"']),
}
NAV = {"Store": "https://store.google.com", "Phones": "https://store.google.com/phones",
       "Result: Cats - Wikipedia": "https://en.wikipedia.org/wiki/Cat", "I'm Feeling Lucky": "https://www.google.com"}


def snapshot_of(url):
    title, lines = PAGES.get(url, PAGES["https://www.google.com"])
    return title, "\n".join(f"- {line} [ref=e{i}]" for i, line in enumerate(lines, 1))


class Element:
    def __init__(self, page, ref):
        self.page, self.ref = page, ref

    def label(self):
        _t, snap = snapshot_of(self.page.url)
        m = re.search(rf'"([^"]*)" \[ref={self.ref}\]', snap)
        return m.group(1) if m else ""

    async def click(self, **kw):
        label = self.label()
        self.page.world.actions.append(("browser_click", label))
        if label in NAV:
            self.page.url = NAV[label]

    async def fill(self, text, **kw):
        self.page.world.actions.append(("browser_type", self.label(), text))
        self.page.typed = text

    async def press(self, key, **kw):
        if key == "Enter":
            self.page.url = f"https://www.google.com/search?q={self.page.typed}"

    async def evaluate(self, *a, **kw):
        return {"t": "search", "a": "", "n": ""}

    async def aria_snapshot(self, **kw):
        return snapshot_of(self.page.url)[1]

    async def count(self):
        return 0


class Page:
    def __init__(self, world):
        self.world, self.closed, self.url, self.typed = world, False, "about:blank", ""

    def is_closed(self):
        return self.closed

    def on(self, *a):
        pass

    async def goto(self, url, **kw):
        self.world.actions.append(("browser_open", url))
        self.url = url

    async def bring_to_front(self):
        self.world.actions.append(("browser_open", "(focused)"))

    async def wait_for_load_state(self, *a, **kw):
        pass

    async def evaluate(self, *a, **kw):
        return "1:1"

    async def title(self):
        return snapshot_of(self.url)[0]

    def locator(self, selector):
        return Element(self, selector.split("=", 1)[1] if selector.startswith("aria-ref=") else "")

    def get_by_role(self, *a, **kw):
        return Element(self, "")


class Context:
    def __init__(self, world):
        self.world, self.handlers, self.pages = world, {}, [Page(world)]

    async def route(self, *a):
        pass

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    async def new_page(self):
        self.pages.append(Page(self.world))
        return self.pages[-1]

    def closed_by_user(self):
        for p in self.pages:
            p.closed = True
        for h in self.handlers.get("close", []):
            h(self)

    async def close(self):
        self.closed_by_user()


class World:
    def __init__(self):
        self.actions, self.contexts, self.launches = [], [], 0


@pytest.fixture
def chrome(monkeypatch):
    world = World()

    class Chromium:
        async def launch_persistent_context(self, path, **kw):
            world.launches += 1
            world.contexts.append(Context(world))
            return world.contexts[-1]

    class PW:
        chromium = Chromium()

        async def stop(self):
            pass

    class Starter:
        async def start(self):
            return PW()

    import playwright.async_api as api
    monkeypatch.setattr(api, "async_playwright", lambda: Starter())
    return world


# ---- a simulated model and simulated free-tier providers --------------------------------------------------
_LABEL = re.compile(r'\[(e\d+)\] "([^"]*)"')


def decide(user: str, system: str):
    """What a capable model does with this request, given only what it was told."""
    u = user.lower().strip(" ?.!")
    if "open chrome" in u:
        return "browser_open", {}
    block = system[system.find("Browser open"):] if "Browser open" in system else ""
    labels = _LABEL.findall(block)
    if "first result" in u:
        return next((("browser_click", {"ref": r}) for r, lab in labels if lab.lower().startswith("result")), None)
    m = re.search(r"search for (.+)", u)
    if m:
        return next((("browser_type", {"ref": r, "text": m[1], "submit": True})
                     for r, lab in labels if "search" in lab.lower()), None)
    target = re.sub(r"^(now )?(you should )?(please )?(click|tap)?( on)?( the)? ?", "", u).strip()
    if target:
        return next((("browser_click", {"ref": r}) for r, lab in labels
                     if lab.lower().rstrip("s") == target.rstrip("s")), None)
    return None


class SimProvider:
    def __init__(self, name, behaviour, latency, log):
        self.name, self.behaviour, self.latency, self.log = name, behaviour, latency, log

    async def stream(self, messages, tools):
        self.log.append(self.name)
        await asyncio.sleep(self.latency)
        if self.behaviour == "429":
            raise LLMError(f"{self.name}: HTTP 429 rate limited", status=429)
        if self.behaviour == "hang":
            await asyncio.sleep(3600)
        size = sum(len(m.content or "") for m in messages) + sum(len(t.description) + 80 for t in tools)
        if self.behaviour == "small" and size > 30000:  # Groq-like per-minute token limit
            raise llm_error(f"{self.name}: HTTP 413 Request too large ... tokens per minute (TPM)", status=413)
        if messages[-1].role == "tool":
            yield TextDelta("Done, sir.")
            return
        offered = {t.name for t in tools}
        choice = decide(messages[-1].content, messages[0].content)
        if choice is None:
            yield TextDelta("Click on what, sir?")
            return
        tool, args = choice
        if tool not in offered:  # Groq rejects a call to a tool the request did not offer
            raise llm_error(f"{self.name}: HTTP 400 tool call validation failed: attempted to call tool '{tool}' "
                            "which was not in request.tools", status=400)
        yield ToolCallEvent(ToolCall("c1", tool, args))


def build(tmp_path, behaviours: dict[str, str], latencies: dict[str, float] | None = None):
    ctx = build_context(tmp_path)
    data = ctx.store.config.model_dump(mode="json")
    data["providers"] = [{"name": n, "model": "m", "tier": "deep" if b == "deep" else "fast",
                          "timeout_s": 180 if b == "deep" else 30} for n, b in behaviours.items()]
    data["plugins"] = {"browser": {"enabled": True, "headless": True}}
    data["turn"]["first_token_deadline_s"] = DEADLINE
    data["turn"]["notice_after_s"] = 0
    ctx.store.update(data)
    for n in behaviours:
        ctx.secrets.set(n, f"key-{n}-0123456789")
    calls: list[str] = []
    lat = latencies or {}
    ctx.router._factory = lambda cfg, key: SimProvider(
        cfg.name, "hang" if behaviours[cfg.name] == "deep" else behaviours[cfg.name], lat.get(cfg.name, 0.03), calls)
    ctx.host.reload()
    return ctx, calls


async def run_replay(ctx, chrome, close_after: int | None = None):
    """Each turn: (spoken reply, time to first sentence, browser actions it caused)."""
    results = []
    for i, turn in enumerate(REPLAY):
        if close_after is not None and i == close_after:
            chrome.contexts[-1].closed_by_user()  # the user closes the Chrome window
            await asyncio.sleep(0)
        before = len(chrome.actions)
        start, first = time.monotonic(), None
        out = []
        with turn_scope():
            async for sentence in ctx.agent.respond(turn["say"]):
                first = first or time.monotonic()
                out.append(sentence)
        results.append((" ".join(out), (first or time.monotonic()) - start, chrome.actions[before:]))
    return results


def turn_worked(turn, result) -> bool:
    expect, (reply, _t, actions) = turn["expect"], result
    if expect.get("question"):
        return not actions and reply.endswith("?")
    if not actions:
        return False
    tool, *rest = actions[-1]
    return tool == expect["tool"] and ("label" not in expect or (rest and rest[0] == expect["label"]))


SIX = ["p1", "p2", "p3", "p4", "p5", "p6"]


def trial_providers(seed: int):
    """Three of six fast providers rate-limited (the day's 429 storm), one Groq-like with a size limit, and a deep
    model that would wait 180 s."""
    rng = random.Random(seed)
    limited = rng.sample(SIX, 3)
    healthy = [n for n in SIX if n not in limited]
    rng.shuffle(healthy)
    behaviours = {n: "429" for n in limited}  # worst case: the rate-limited ones are first in line
    behaviours.update({n: "ok" for n in healthy})
    behaviours[healthy[0]] = "small"
    behaviours["deep"] = "deep"
    latencies = {n: rng.uniform(0.02, 0.08) for n in SIX}
    return behaviours, latencies


# ---- criteria 1, 2, 5, 6 ----------------------------------------------------------------------------------
async def test_browser_follow_ups_work_in_at_least_9_of_10_trials(tmp_path, chrome):
    worked_trials, first_sentence, llm_calls, deep_calls = 0, [], [], 0
    for seed in range(10):
        chrome.actions.clear()
        behaviours, latencies = trial_providers(seed)
        ctx, calls = build(tmp_path / f"t{seed}", behaviours, latencies)
        results = await run_replay(ctx, chrome, close_after=2 if seed % 2 else None)
        worked = [turn_worked(t, r) for t, r in zip(REPLAY, results)]
        worked_trials += all(worked)
        first_sentence += [r[1] for r in results[1:]]
        ctx.services["tracer"].flush()
        llm_calls += [e for e in read_events(tmp_path / f"t{seed}" / "traces") if e["kind"] == "llm_call"]
        deep_calls += calls.count("deep")
        await ctx.host.call("browser_close", {})
    assert worked_trials >= 9, f"only {worked_trials}/10 trials worked"
    # criterion 2: three of six providers rate-limited, p90 time to the first spoken sentence within target
    first_sentence.sort()
    p90 = first_sentence[int(0.9 * (len(first_sentence) - 1))]
    assert p90 <= P90_TARGET, f"p90 {p90:.3f}s > {P90_TARGET}s (scaled)"
    # criterion 6 and the replay check: no tool-not-offered errors, no long fast-tier waits, no deep model
    assert not [e for e in llm_calls if e.get("error_kind") == "tool_not_offered"]
    assert max(e["total_ms"] for e in llm_calls) <= DEADLINE * 1000 + 150
    assert deep_calls == 0


async def test_closing_the_window_then_open_chrome_works_without_a_restart(tmp_path, chrome):
    behaviours, latencies = trial_providers(1)
    ctx, _ = build(tmp_path, behaviours, latencies)
    results = await run_replay(ctx, chrome, close_after=2)
    assert chrome.launches == 2 and turn_worked(REPLAY[2], results[2])
    await ctx.host.call("browser_close", {})


async def test_the_day_before_the_fixes_fails(tmp_path, chrome):
    """Sanity check of the replay itself: with the fixes switched off it reproduces the stuck follow-ups."""
    behaviours, latencies = trial_providers(0)
    ctx, _ = build(tmp_path, behaviours, latencies)
    data = ctx.store.config.model_dump(mode="json")
    data["plugins"]["browser"].update(prompt_state=False, fastpath=False)
    data["turn"].update(request_diet=False)
    data["router"].update(recover_request_errors=False)
    ctx.store.update(data)
    ctx.host.reload()
    ctx.services["live_sessions"].pop("browser", None)
    results = await run_replay(ctx, chrome)
    assert not all(turn_worked(t, r) for t, r in zip(REPLAY, results))
    await ctx.host.call("browser_close", {})


# ---- criteria 3 and 4: through the voice pipeline ---------------------------------------------------------
def voice(ctx, texts, player_delay=0.0):
    f = type("F", (), {})()
    f.audio, f.wake, f.endpointer, f.stt = FakeAudioIn(), FakeWake(), FakeEndpointer(), FakeSTT(texts)
    f.tts, f.player, f.bus = FakeTTS(), FakePlayer(player_delay), EventBus()
    data = ctx.store.config.model_dump(mode="json")
    data["audio"].update(follow_up=False, wake_ack="", barge_in=True)
    ctx.store.update(data)
    p = VoicePipeline(audio_in=f.audio, wake=f.wake, endpointer=f.endpointer, stt=f.stt, tts=f.tts, player=f.player,
                      agent=ctx.agent, bus=f.bus, store=ctx.store, tracer=ctx.services["tracer"])
    return p, f


@pytest.mark.parametrize("behaviour, expected", [
    ("429", BRAIN_DOWN), ("hang", MODELS_SLOW), ("ok", "Click on what, sir?")])
async def test_no_turn_ends_without_speech_or_a_tool_call(tmp_path, chrome, behaviour, expected):
    ctx, _ = build(tmp_path, {n: behaviour for n in SIX})
    p, f = voice(ctx, ["you should click on", "you should click on"])
    task = asyncio.create_task(p.run())
    for _ in range(2):
        before = len(f.player.played)
        f.audio.push(b"WAKE", b"SPEECH", b"END")
        await wait_until(lambda: len(f.player.played) > before, timeout=5)
        await wait_until(lambda: p.state == "idle", timeout=5)
    f.audio.close()
    await asyncio.wait_for(task, 5)
    assert f.player.played == [expected.encode()] * 2


async def test_repeating_open_chrome_while_it_is_slow_does_not_restart_it(tmp_path, chrome):
    ctx, calls = build(tmp_path, {"p1": "ok"}, {"p1": 0.4})
    p, f = voice(ctx, ["Open Chrome", "I said open chrome", "Open Chrome"])
    task = asyncio.create_task(p.run())
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: p.state == "processing", timeout=5)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: len(f.stt.calls) == 2, timeout=5)
    f.audio.push(b"WAKE", b"SPEECH", b"END")
    await wait_until(lambda: b"Done, sir." in f.player.played, timeout=5)
    f.audio.close()
    await asyncio.wait_for(task, 5)
    assert [m.content for m in ctx.agent.history if m.role == "user"] == ["Open Chrome"]
    assert f.player.played.count(STILL_WORKING.encode()) == 1 and chrome.launches == 1
    await ctx.host.call("browser_close", {})
