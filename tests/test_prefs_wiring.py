"""Learning that is real but never silent: habits are offered, accepted by the user, and then change how work is finished."""
from minty_core.context import build_context
from minty_core.gmailwatch import GmailWatcher, WatchSettings, WatchStore
from minty_core.policy import Guard
from minty_core.prefs import PreferenceStore
from tests.plugin_helpers import make_host
from tests.test_gmailwatch import FakeSource, Recorder, msg
from tests.test_policy_mandate import Store
from tests.test_workflow_wiring import EXECUTE, GOAL  # noqa: F401
from minty_core.mcp.registry import McpRegistry, tool_hash


def ready_composio(tmp_path):
    reg = McpRegistry(tmp_path / "mcp.json")
    reg.add_remote("composio", "https://connect.composio.dev/mcp", {"x-consumer-api-key": "COMPOSIO_API_KEY"})
    reg.record_tools("composio", [EXECUTE])
    reg.allow("composio", "COMPOSIO_MULTI_EXECUTE_TOOL", tool_hash(EXECUTE), readonly=False)


async def test_the_tools_list_set_accept_correct_and_forget(tmp_path):
    services = {}
    host = make_host(tmp_path, services=services)
    prefs = services["prefs"]
    assert "Nothing learned yet" in await host.call("prefs_list", {})
    assert "Saved" in await host.call("prefs_set", {"key": "research->sheet", "scope": "research"})
    for ref in "abc":
        prefs.observe("research->doc", "research", ref=ref)
    listing = await host.call("prefs_list", {})
    assert "Keep research in a sheet" in listing and "Suggested" in listing and "Write research up as a document" in listing
    sid = prefs.suggestions()[0].id
    assert "Accepted" in await host.call("prefs_accept", {"id": sid}) and prefs.value("research->doc", ("research",)) == "1"
    assert "trust that habit less" in await host.call("prefs_correct", {"key": "research->doc", "scope": "research"})
    assert "Forgot" in await host.call("prefs_forget", {"id": sid}) and prefs.value("research->doc", ("research",)) is None


async def test_only_the_users_own_words_can_set_a_preference_and_accepting_one_is_asked(tmp_path):
    host = make_host(tmp_path)
    setter, accept = host.get_tool("prefs_set"), host.get_tool("prefs_accept")

    async def verdict(tool, name, args, request):
        asked = []

        async def confirm(q):
            asked.append(q)
            return False

        d = await Guard(Store()).authorize(tool, name, args, confirm, request=request)
        return d.allowed and not asked, asked

    ok, _ = await verdict(setter, "prefs_set", {"key": "research->sheet", "scope": "research"}, "from now on always put my research in a sheet")
    assert ok
    ok, asked = await verdict(setter, "prefs_set", {"key": "research->sheet", "scope": "research"}, "what is the weather")
    assert not ok and asked
    ok, asked = await verdict(accept, "prefs_accept", {"id": 1}, "research the market")  # adopting a habit is the user's yes, asked once
    assert not ok and asked and "habit" in asked[0].lower()


def test_asking_for_a_sheet_in_research_three_times_makes_a_suggestion_and_only_accepting_it_changes_anything(tmp_path):
    ready_composio(tmp_path)
    ctx = build_context(tmp_path)
    data = ctx.store.config.model_dump(mode="json")
    data["mcp"]["enabled"] = True
    ctx.store.update(data)
    brief, prefs = ctx.services["workflow_brief"], ctx.services["prefs"]
    plain = "research the best standing desks"
    for goal in ("research the top 10 vendors and put them in a sheet", "research three laptops and add them to a spreadsheet", "research the best crm and track it in a sheet"):
        brief(goal)
    [s] = prefs.suggestions()
    assert s.key == "research->sheet" and "artifacts_list" not in brief(plain)  # offered, not applied
    prefs.accept(s.id)
    assert "artifacts_list" in brief(plain)  # now research ends in a sheet without the word being said


async def test_mail_you_found_useful_three_times_from_one_sender_is_offered_as_a_vip_and_a_muted_sender_stays_quiet(tmp_path):
    prefs = PreferenceStore(tmp_path / "p.db")
    src, rec = FakeSource(), Recorder(tmp_path)
    store = WatchStore(tmp_path / "w.db")
    settings = WatchSettings(enabled=True, accounts=("me",), me={"me": "me@example.com"})
    watcher = GmailWatcher(src, store, rec.notifier, lambda: settings, prefs=lambda key: prefs.value(key, ("mail",)))
    await watcher.poll_once()
    src.new["me"] = [(f"m{i}", f"t{i}", ("INBOX",)) for i in range(1, 4)]
    src.mails.update({f"m{i}": msg(i, subject=f"Report {i}", snippet="fyi", sender="Sam <sam@acme.test>") for i in range(1, 4)})
    src.head += 1
    await watcher.poll_once()
    assert store.sender_of("t1") == "sam@acme.test"
    for i in range(1, 4):
        prefs.observe(f"mail.vip:{store.sender_of(f't{i}')}", "mail", ref=f"t{i}")
    [s] = prefs.suggestions()
    assert s.key == "mail.vip:sam@acme.test"
    prefs.accept(s.id)
    rec.told.clear()
    src.new["me"] = [("m9", "t9", ("INBOX",))]
    src.mails["m9"] = msg(9, subject="Quick thing", snippet="Can you look at this when you have a minute?", sender="Sam <sam@acme.test>")
    src.head += 1
    await watcher.poll_once()
    assert any(n == "panel" for n, *_ in rec.told) and any("vip" in t.lower() or "always matters" in t for n, s, k, t in rec.told)  # it counts for something now
    prefs.set_explicit("mail.mute:noisy@spam.test", "mail")
    rec.told.clear()
    src.new["me"] = [("m10", "t10", ("INBOX",))]
    src.mails["m10"] = msg(10, subject="Invoice due Friday", snippet="Please pay by Friday. Can you confirm?", sender="Noisy <noisy@spam.test>")
    src.head += 1
    await watcher.poll_once()
    assert [x for x in rec.told if x[0] in ("say", "phone", "panel")] == []  # asked to be quiet about this sender
