"""The sheets and docs Minty may write to: listed, adopted by the user's own words, forgotten, tagged."""
from minty_core.policy import Guard
from tests.plugin_helpers import make_host
from tests.test_policy_mandate import Store

SHEET = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"
LINK = f"https://docs.google.com/spreadsheets/d/{SHEET}/edit#gid=0"


async def test_the_tools_and_an_empty_list(tmp_path):
    host = make_host(tmp_path)
    names = {s.name for s in host.tool_specs() if s.name.startswith("artifacts_")}
    assert names == {"artifacts_list", "artifacts_register", "artifacts_forget", "artifacts_tag"}
    assert "No sheets or docs" in await host.call("artifacts_list", {})


async def test_a_registered_sheet_is_listed_found_by_its_key_tagged_and_forgotten(tmp_path):
    host = make_host(tmp_path)
    out = await host.call("artifacts_register", {"ref": LINK, "title": "Leads", "key": "leads"})
    assert "Leads" in out and SHEET not in out.replace(LINK, "")  # the id is not repeated, the link is enough
    listing = await host.call("artifacts_list", {})
    assert "Leads" in listing and "sheet" in listing and "leads" in listing and "you named it" in listing and LINK.split("#")[0] in listing
    assert "Leads" in await host.call("artifacts_list", {"key": "leads"}) and "No sheets or docs" in await host.call("artifacts_list", {"key": "nope"})
    assert "Tagged" in await host.call("artifacts_tag", {"ref": SHEET, "key": "q4-leads"})
    assert "Leads" in await host.call("artifacts_list", {"key": "q4-leads"})
    assert "Forgot" in await host.call("artifacts_forget", {"ref": SHEET})
    assert "No sheets or docs" in await host.call("artifacts_list", {})
    assert "not one I know" in await host.call("artifacts_forget", {"ref": SHEET})


async def test_a_bad_reference_is_refused_in_words(tmp_path):
    host = make_host(tmp_path)
    assert "not a Google" in await host.call("artifacts_register", {"ref": "https://evil.test/x"})
    assert "not a Google" in await host.call("artifacts_register", {"ref": "hello"})


async def test_only_the_users_own_words_can_name_a_sheet_to_write_to(tmp_path):
    spec = make_host(tmp_path).get_tool("artifacts_register")

    async def asked_for(request):
        seen = []

        async def confirm(q):
            seen.append(q)
            return False

        d = await Guard(Store()).authorize(spec, "artifacts_register", {"ref": LINK}, confirm, request=request)
        return d, seen

    d, seen = await asked_for(f"use this sheet for the leads: {LINK}")
    assert d.allowed and not seen
    d, seen = await asked_for("track my leads in a google sheet")  # the link came from somewhere else
    assert seen and not d.allowed
    d, seen = await asked_for("")
    assert seen and not d.allowed
