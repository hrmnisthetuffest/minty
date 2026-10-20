"""Click risk classes on real pages: declining cookies is free, money always asks."""
from tests.agent_eval.conftest import needs_chrome
from tests.agent_eval.scripted import ref_of
from tests.plugin_helpers import make_host


@needs_chrome
async def test_click_risk_on_the_fixture_shop(tmp_path, site):
    host = make_host(tmp_path / "cfg", plugins={"browser": {"enabled": True, "headless": True, "allow_local": True}})
    risk = host.get_tool("browser_click").risk
    try:
        page = await host.call("browser_open", {"url": site.url})
        assert "I closed a pop-up for you: Reject non-essential" in page  # never a question, never 'accept all'
        assert "Accept all" not in page
        page = await host.call("browser_open", {"url": site.url + "/p/1"})
        assert risk({"ref": ref_of(page, "Add to cart")}) == 0
        assert risk({"ref": ref_of(page, "Buy now")}) == 3
        page = await host.call("browser_open", {"url": site.url + "/checkout"})
        assert risk({"ref": ref_of(page, "Place order")}) == 3
    finally:
        await host.call("browser_close", {})
