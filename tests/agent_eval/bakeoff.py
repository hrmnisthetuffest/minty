"""Run the task suite once per model (k tries each) and tabulate. Used by scripts/eval_agent.py."""
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Awaitable, Callable

from tests.agent_eval.metrics import summarise
from tests.agent_eval.tasks import Task

log = logging.getLogger(__name__)


def single_provider_router(store, secrets, name: str):
    """A router that can only use one provider, so a rate-limited model never silently hands over to another."""
    from minty_core.llm.router import ProviderRouter

    cfg = store.config.model_copy(deep=True)
    cfg.providers = [p for p in cfg.providers if p.name == name]
    if not cfg.providers:
        raise SystemExit(f"no provider called {name!r} in config.toml")
    cfg.preferred_provider = ""
    return ProviderRouter(SimpleNamespace(config=cfg), secrets)


def _done(path: Path) -> set[tuple[str, str, int]]:
    seen = set()
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not row.get("infra"):
                seen.add((row["model"], row["task"], row["trial"]))
    return seen


async def bakeoff(models: list[str], tasks: list[Task], k: int, make_router: Callable[[str], object],
                  out: Path, run: Callable[..., Awaitable[dict]], pause_s: float = 2.0,
                  infra_retries: int = 2, infra_wait_s: float = 30.0) -> dict[str, dict]:
    out.parent.mkdir(parents=True, exist_ok=True)
    done = _done(out)
    for model in models:
        router = make_router(model)
        for task in tasks:
            for trial in range(k):
                if (model, task.id, trial) in done:
                    continue
                for attempt in range(infra_retries + 1):
                    row = await run(task, router)
                    if not row.get("infra") or attempt == infra_retries:
                        break
                    log.info("%s rate-limited/unavailable; waiting %.0fs", model, infra_wait_s)
                    await asyncio.sleep(infra_wait_s)
                row.update(model=model, trial=trial)
                with out.open("a") as f:
                    f.write(json.dumps(row) + "\n")
                await asyncio.sleep(pause_s)
    return table_from(out, k)


def table_from(out: Path, k: int) -> dict[str, dict]:
    rows = [json.loads(l) for l in out.read_text().splitlines() if l.strip()] if out.exists() else []
    models = sorted({r["model"] for r in rows})
    return {m: summarise([r for r in rows if r["model"] == m], k=k) for m in models}


def markdown(table: dict[str, dict], k: int) -> str:
    head = f"| model | pass@1 | pass^{k} | forbidden | median steps | median s | tokens (est) | infra | failed tasks |"
    lines = [head, "|" + "---|" * 9]
    for model, s in table.items():
        lines.append(f"| {model} | {s['pass_at_1']} | {s.get(f'pass_hat_{k}')} | {s['forbidden_hits']} | "
                     f"{s['median_steps']} | {s['median_seconds']} | {s['tokens_est']:,} | {s['infra_errors']} | "
                     f"{', '.join(s['failed_tasks']) or '—'} |")
    return "\n".join(lines)
