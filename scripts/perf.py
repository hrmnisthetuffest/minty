#!/usr/bin/env python3
"""Measure Minty's hot paths against scripts/perf_budget.toml. Exit 1 if anything is over budget.

  .venv/bin/python scripts/perf.py             # measure and compare
  .venv/bin/python scripts/perf.py --budget my.toml
Use it before and after any optimisation: no speed-up is merged without a number from here.
"""
import argparse
import asyncio

import numpy as np
import resource
import subprocess
import sys
import time
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def per_call(fn, n: int) -> float:
    """Seconds per call, best of 3 runs."""
    best = float("inf")
    for _ in range(3):
        start = time.perf_counter()
        for _ in range(n):
            fn()
        best = min(best, (time.perf_counter() - start) / n)
    return best


def prompt_tokens(request: str) -> float:
    """System prompt + tool schemas one request would send, with every plugin on (about 4 characters a token)."""
    import json
    import tempfile

    import minty_core.plugins.builtin as builtin
    from minty_core.context import build_context

    ctx = build_context(Path(tempfile.mkdtemp()))
    data = ctx.store.config.model_dump(mode="json")
    data["plugins"] = {p.parent.name: {"enabled": True} for p in Path(builtin.__file__).parent.glob("*/plugin.py")}
    ctx.store.update(data)
    chars = len(ctx.agent._system_prompt(request)) + sum(
        len(json.dumps({"name": s.name, "description": s.description, "parameters": s.parameters}))
        for s in ctx.agent._tool_specs(request))
    return chars / 4




def tool_schema_chars() -> dict[str, float]:
    """Characters of tool schemas (name, description, parameters): the core set sent with every request, and all
    tools with every plugin on."""
    import json
    import tempfile

    import minty_core.plugins.builtin as builtin
    from minty_core.context import build_context
    from minty_core.toolkits import CORE_TOOLS

    ctx = build_context(Path(tempfile.mkdtemp()))
    data = ctx.store.config.model_dump(mode="json")
    data["plugins"] = {p.parent.name: {"enabled": True} for p in Path(builtin.__file__).parent.glob("*/plugin.py")}
    ctx.store.update(data)
    size = {s.name: len(s.name) + len(s.description) + len(json.dumps(s.parameters)) for s in ctx.host.tool_specs()}
    return {"tool_chars_core": float(sum(v for k, v in size.items() if k in CORE_TOOLS)),
            "tool_chars_all": float(sum(size.values()))}


def browser_followup(diet: bool = True, budget: int | None = None) -> dict[str, float]:
    """'click store' after three page readings with the browser window open: characters sent (system prompt +
    conversation, as the llm_call trace counts them) and how many tools are offered. budget: characters one page
    reading may take (None = the browser plugin's own default; 0 = unlimited, the reading before S-1)."""
    import tempfile

    import minty_core.plugins.builtin as builtin
    from minty_core.agent import shape_result
    from minty_core.context import build_context
    from minty_core.llm.types import Message, ToolCall
    from minty_core.plugins.builtin.browser.plugin import PAGE_BUDGET
    from minty_core.webpage import parse_aria, render_page

    ctx = build_context(Path(tempfile.mkdtemp()))
    data = ctx.store.config.model_dump(mode="json")
    data["plugins"] = {p.parent.name: {"enabled": True} for p in Path(builtin.__file__).parent.glob("*/plugin.py")}
    data["turn"]["request_diet"] = diet
    ctx.store.update(data)
    ctx.services.setdefault("live_sessions", {})["browser"] = lambda: True  # the window is open
    snap = "\n".join(f'- link "Product {i} deluxe desk lamp" [ref=e{i}] [cursor=pointer]' for i in range(120))
    reading, _ = render_page(parse_aria(snap + "\n- paragraph: " + "warm white light, adjustable arm " * 120),
                             "Desk lamps", "https://shop.example/s?k=desk+lamp",
                             budget=PAGE_BUDGET if budget is None else budget)
    agent = ctx.agent
    for i, request in enumerate(("open the shop", "search for desk lamps", "sort by price")):
        agent.toolkits.next_turn()
        agent.history += [Message("user", request),
                          Message("assistant", "", tool_calls=[ToolCall(f"c{i}", "browser_look", {})]),
                          Message("tool", shape_result(reading), tool_call_id=f"c{i}", name="browser_look"),
                          Message("assistant", "Done.")]
        agent.toolkits.used("browser_look")
    agent.toolkits.next_turn()
    messages = agent._outgoing("click store")
    return {"browser_followup_chars": float(sum(len(m.content or "") for m in messages)),
            "browser_followup_tools": float(len(agent._tool_specs("click store")))}


def turn_costs() -> dict[str, float]:
    """What each request costs before the model is called, with every plugin on and a long memory."""
    import tempfile
    from datetime import datetime, timedelta

    import minty_core.plugins.builtin as builtin
    from minty_core.context import build_context

    ctx = build_context(Path(tempfile.mkdtemp()))
    data = ctx.store.config.model_dump(mode="json")
    data["plugins"] = {p.parent.name: {"enabled": True} for p in Path(builtin.__file__).parent.glob("*/plugin.py")}
    ctx.store.update(data)
    gists = ctx.services["gists"]
    for i in range(2000):
        gists.add("day", f"Talked about topic {i} number {i * 7} and maths homework fractions plan {i % 13}",
                  source_ref=f"d{i}")
    request = "what is the maths homework for tomorrow"
    out = {"turn_prep_ms": per_call(lambda: (ctx.agent._system_prompt(request), ctx.agent._tool_specs(request)), 100)
           * 1000, "gist_search_ms": per_call(lambda: gists.search(request), 100) * 1000}
    sched = ctx.services["scheduler"]
    now = datetime.now()
    for i in range(200):
        sched.add(f"item {i}", now + timedelta(days=1, minutes=i), user_set=False, kind="nudge", key=f"k{i}")
    out["scheduler_tick_ms"] = per_call(lambda: sched.tick(now), 50) * 1000
    return out


def measure() -> dict[str, float]:
    out = {}
    start = time.perf_counter()
    subprocess.run([sys.executable, "-c", "import minty_core.app"], check=True, cwd=ROOT,
                   env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
    out["import_app_ms"] = (time.perf_counter() - start) * 1000

    from minty_core.audio.endpointer import EnergyEndpointer
    from minty_core.audio.types import FRAME_BYTES
    from minty_core.audio.wakewords import TranscriptWakeDetector
    from minty_core.config import AudioConfig
    from minty_core.events import EventBus
    from minty_core.tui import TuiModel, orb, render

    cfg = AudioConfig()
    silent = bytes(FRAME_BYTES)
    wake = TranscriptWakeDetector(lambda pcm: "", lambda: cfg)
    out["wake_frame_us"] = per_call(lambda: wake.detect(silent), 2000) * 1e6
    ep = EnergyEndpointer(lambda: cfg)
    out["endpointer_frame_us"] = per_call(lambda: (ep.feed(silent), ep.reset()), 2000) * 1e6

    async def bus_cost():
        bus = EventBus()
        queues = [bus.subscribe({"level"}) for _ in range(3)]

        def publish():
            bus.publish("level", db=-60.0, peak_db=-50.0, threshold_db=-40.0, speech=False)
            for q in queues:
                while not q.empty():
                    q.get_nowait()
        return per_call(publish, 2000)
    out["bus_publish_us"] = asyncio.run(bus_cost()) * 1e6

    model = TuiModel()
    model.state = "thinking"
    out["orb_frame_ms"] = per_call(lambda: orb(model, time.monotonic()), 50) * 1000
    from rich.console import Console
    console = Console(width=100, height=30, file=open("/dev/null", "w"))
    out["tui_render_ms"] = per_call(lambda: console.print(render(model, 100, 30)), 20) * 1000

    out.update(turn_costs())
    from minty_core.tui import IDLE_FPS

    out["tui_idle_cpu_pct"] = out["tui_render_ms"] * IDLE_FPS / 10  # share of one core while nothing is moving
    from minty_core.audio.vad import FrameVAD

    vad, frame = FrameVAD(), (np.random.default_rng(0).normal(0, 300, FRAME_BYTES // 2)).astype(np.int16).tobytes()
    out["frame_vad_us"] = per_call(lambda: vad(frame), 200) * 1e6  # only for frames between room and voice level
    out["prompt_tokens_plain"] = prompt_tokens("what time is it")
    out["prompt_tokens_shopping"] = prompt_tokens("find me the cheapest desk lamp on amazon")
    out.update(browser_followup())
    out["browser_followup_chars_unbudgeted"] = browser_followup(budget=0)["browser_followup_chars"]
    out.update(tool_schema_chars())

    import minty_core.app  # noqa: F401  (already measured cold above; this is for RSS)
    out["rss_after_import_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--budget", default=str(ROOT / "scripts" / "perf_budget.toml"))
    args = p.parse_args(argv)
    budget = tomllib.loads(Path(args.budget).read_text())
    results = measure()
    over = []
    print(f"{'metric':<22}{'value':>12}{'budget':>12}")
    for name, value in results.items():
        limit = budget.get(name)
        flag = "" if limit is None or value <= limit else "  OVER"
        if flag:
            over.append(name)
        print(f"{name:<22}{value:>12.2f}{(limit if limit is not None else float('nan')):>12.2f}{flag}")
    return 1 if over else 0


if __name__ == "__main__":
    raise SystemExit(main())
