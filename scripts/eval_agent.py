#!/usr/bin/env python3
"""Measure the browser agent on the fixture shop, per model.

  .venv/bin/python scripts/eval_agent.py --models gemini,groq --k 3
  .venv/bin/python scripts/eval_agent.py --tasks delayed,scroll --models gemini --k 1 --show

Results append to eval-results/<run>.jsonl (re-running the same --run resumes); a Markdown table is printed and
saved next to it. Each model is tested alone: no failover to another provider, so a rate limit shows as
'infra', never as a model failure.
"""
import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.agent_eval.bakeoff import bakeoff, markdown, single_provider_router  # noqa: E402
from tests.agent_eval.fixture_site import FixtureSite  # noqa: E402
from tests.agent_eval.runner import run_task  # noqa: E402
from tests.agent_eval.tasks import BY_ID, TASKS  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--models", required=True, help="comma-separated provider names from config.toml")
    p.add_argument("--tasks", default="", help="comma-separated task ids (default: all)")
    p.add_argument("--k", type=int, default=3)
    p.add_argument("--run", default=time.strftime("%Y-%m-%d"), help="run name (same name = resume)")
    p.add_argument("--config", default=os.path.expanduser("~/.config/minty"))
    p.add_argument("--show", action="store_true", help="show the browser window")
    p.add_argument("--pause", type=float, default=2.0, help="seconds between runs (free-tier pacing)")
    args = p.parse_args(argv)

    from minty_core.config import ConfigStore
    from minty_core.keystore import SecretsStore

    store = ConfigStore(Path(args.config))
    store.load()
    secrets = SecretsStore(Path(args.config))
    tasks = [BY_ID[t] for t in args.tasks.split(",") if t] if args.tasks else TASKS
    out = Path("eval-results") / f"{args.run}.jsonl"

    async def go():
        with FixtureSite() as site:
            async def run(task, router):
                return await run_task(task, router, headless=not args.show, site=site)
            return await bakeoff(args.models.split(","), tasks, args.k,
                                 lambda name: single_provider_router(store, secrets, name), out, run,
                                 pause_s=args.pause)

    table = asyncio.run(go())
    text = markdown(table, args.k)
    out.with_suffix(".md").write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
