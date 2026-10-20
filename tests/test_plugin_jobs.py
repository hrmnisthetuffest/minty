import asyncio

from tests.plugin_helpers import make_host
from minty_core.jobs import JobRunner


async def slow(ctx):
    ctx.progress("reading the reviews")
    await asyncio.sleep(10)


async def test_status_and_cancel_tools(tmp_path):
    runner = JobRunner(None)
    host = make_host(tmp_path, services={"jobs": runner})
    assert await host.call("jobs_status", {}) == "I'm not working on anything in the background."
    runner.submit("browser", "compare lamps", slow)
    runner.submit("think", "is it worth it", slow)
    await asyncio.sleep(0.02)
    detail = await host.call("jobs_status", {"id": 1})
    assert detail.startswith("browser job 1 (compare lamps): running") and "reading the reviews" in detail
    assert await host.call("jobs_cancel", {}) == "Stopped deep thinking 2."
    assert await host.call("jobs_cancel", {"all": True}) == "Stopped 1 job(s)."
    assert await host.call("jobs_cancel", {}) == "Nothing to cancel."
    assert "There is no job 9" in await host.call("jobs_status", {"id": 9})


def test_tools_are_safe_for_live_mode(tmp_path):
    host = make_host(tmp_path, services={"jobs": JobRunner(None)})
    for name in ("jobs_status", "jobs_cancel"):
        tool = host.get_tool(name)
        assert tool is not None and not tool.risky and not tool.sensitive and tool.confirm_when is None
