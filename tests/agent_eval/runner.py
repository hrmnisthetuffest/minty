"""Run one task through the real browser plugin and judge it from the fixture's server state."""
import tempfile
import time
from pathlib import Path

from tests.agent_eval.fixture_site import FixtureSite
from tests.agent_eval.tasks import Task
from tests.plugin_helpers import make_host

INFRA_MARKERS = ("no model could drive the browser", "no llm provider available", "no language model")


class Recorder:
    """Collects trace events of one run (both the router's and the plugin's)."""

    def __init__(self, inner=None):
        self.events: list[dict] = []
        self._inner = inner

    def event(self, kind, **data):
        self.events.append({"kind": kind, **data})
        if self._inner is not None:
            self._inner.event(kind, **data)


async def run_task(task: Task, router, *, prefer: str | None = None, headless: bool = True,
                   site: FixtureSite | None = None) -> dict:
    own_site = site is None
    site = site or FixtureSite().__enter__()
    site.state.reset()
    recorder = Recorder()
    previous = getattr(router, "tracer", None)
    router.tracer = recorder
    asked: list[str] = []

    async def confirm(question: str) -> bool:
        asked.append(question)
        return task.confirm

    settings = {"enabled": True, "headless": headless, "allow_local": True}
    if prefer:
        settings["agent_model"] = prefer
    with tempfile.TemporaryDirectory(prefix="minty-eval-") as tmp:
        host = make_host(Path(tmp), plugins={"browser": settings},
                         services={"router": router, "confirm": confirm, "tracer": recorder})
        started = time.monotonic()
        try:
            reply = await host.call("browser_task", {"goal": task.text(site.url), "wait": True})
        finally:
            seconds = time.monotonic() - started
            await host.call("browser_close", {})
            router.tracer = previous
            if own_site:
                site.__exit__(None, None, None)
    llm = [e for e in recorder.events if e["kind"] == "llm_call"]
    steps = [e for e in recorder.events if e["kind"] == "tool_call" and e.get("job") == "browser_task"]
    infra = any(m in reply.lower() for m in INFRA_MARKERS)
    return {
        "task": task.id, "category": task.category, "passed": (not infra) and task.predicate(site.state, reply),
        "forbidden_hit": task.forbidden(site.state), "steps": len(steps), "llm_calls": len(llm),
        "tool_errors": sum(1 for e in steps if not e.get("ok")),
        "tokens_est": sum((e.get("chars_in") or 0) + (e.get("chars_out") or 0) for e in llm) // 4,
        "seconds": round(seconds, 1), "asked": asked, "reply": reply[:400], "infra": infra,
        "providers": sorted({e.get("provider") for e in llm if e.get("outcome") == "ok"}),
        # how the loop reasoned: plan size, each verdict with its reason, recoveries, how it ended
        "loop": [{k: v for k, v in e.items() if k != "ts"} for e in recorder.events
                 if e["kind"] in ("plan", "verify", "recover", "loop_end")][:30],
        "actions": [f"{e.get('name')} {str(e.get('args'))[:70]}" for e in steps][:40],
    }
