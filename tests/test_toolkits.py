import pytest

from minty_core.toolkits import CORE_TOOLS, NEED_TOOLKIT, ToolkitRouter

PLUGINS = {
    "clock": ["clock_get_time"], "memory": ["memory_remember", "memory_recall"], "web_search": ["web_search_search"],
    "weather": ["weather_get_weather"], "timers": ["timers_set_timer", "timers_list_timers"],
    "jobs": ["jobs_status", "jobs_cancel"], "deep_think": ["deep_think_think_in_background"],
    "missions": ["missions_start", "missions_answer", "missions_resume", "missions_report"],
    "display": ["display_show", "display_clear", "display_open_url"],
    "browser": ["browser_task", "browser_open", "browser_click", "browser_look"],
    "spotify": ["spotify_play", "spotify_pause", "spotify_volume"], "music_id": ["music_id_identify"],
    "smart_home": ["smart_home_set_device", "smart_home_control_secure"],
    "files": ["files_list", "files_download"], "agents": ["agents_start_task"], "installer": ["installer_install"],
    "journal": ["journal_add_entry"], "history": ["history_search"], "plantcare": ["plantcare_water_plants"],
}


def router(**kw):
    return ToolkitRouter(lambda: PLUGINS, **kw)


def offered(r, text, **kw):
    return r.tool_names(text, **kw) - set(CORE_TOOLS)


def test_a_plain_question_gets_only_the_core_tools():
    r = router()
    assert r.select("what's two plus two") == []
    assert r.tool_names("tell me a joke") == set(CORE_TOOLS) & {t for ts in PLUGINS.values() for t in ts}


@pytest.mark.parametrize("text, kit", [
    ("play some music", "music"), ("turn off the bedroom lamp", "home"), ("unlock the door", "home"),
    ("open amazon and find a mug", "browser"), ("download the report and save it to a folder", "files"),
    ("what song is this", "music"), ("install ripgrep", "system"), ("what did we talk about yesterday", "notes"),
    ("start a coding project", "code"), ("turn the volume down", "music"),
])
def test_requests_pick_their_toolkit(text, kit):
    assert kit in router().select(text)


def test_at_most_three_toolkits_and_no_tools_from_the_rest():
    r = router()
    text = "open chrome, play music, turn off the light, download a file and install a package"
    assert len(r.select(text)) == 3
    assert "installer_install" not in offered(router(), "play some music")


def test_follow_ups_stay_on_the_toolkit_for_two_turns():
    r = router()
    r.next_turn()
    assert "spotify_play" in offered(r, "play some jazz")
    r.used("spotify_play")
    r.next_turn()
    assert "spotify_pause" in offered(r, "yes do that")  # no music word at all
    r.next_turn()
    assert "spotify_pause" not in offered(r, "and the weather?")


def test_need_toolkit_loads_it_and_records_the_miss():
    events = []

    class T:
        def event(self, kind, **data):
            events.append((kind, data))

    r = router(tracer=T())
    assert "smart_home_set_device" not in offered(r, "make it cosy in here")
    assert "Loaded home" in r.need("home", "make it cosy in here")
    assert "smart_home_set_device" in offered(r, "make it cosy in here")
    assert events == [("toolkit_miss", {"wanted": "home", "selected": [], "request": "make it cosy in here"})]
    assert r.need("nonsense").startswith("There is no tool group")
    r.reset()
    assert "smart_home_set_device" not in offered(r, "hello")


def test_unlisted_plugin_becomes_its_own_toolkit():
    r = router()
    assert r.kits()["plantcare"] == ["plantcare_water_plants"]
    assert "plantcare_water_plants" in offered(r, "did I water the plants")


def test_need_toolkit_spec_lists_what_exists_briefly():
    spec = router().spec()
    assert spec.name == NEED_TOOLKIT and "music (play music" in spec.description
    assert "plantcare" in spec.parameters["properties"]["name"]["enum"]
    assert len(spec.description) < 700


def test_using_browser_task_marks_the_browser_toolkit():
    r = router()
    r.next_turn()
    r.used("browser_task")  # a core tool, but it is the browser
    r.next_turn()
    assert "browser_click" in offered(r, "store")


def test_browser_tools_stay_while_a_session_is_open():
    session = {"open": True}
    r = router(live=lambda: {"browser"} if session["open"] else set())
    r.next_turn()
    assert "browser_click" in offered(r, "open chrome")
    r.used("browser_open")
    for word in ("store", "hmm", "what", "yes", "phones"):  # one-word follow-ups and misheard turns
        r.next_turn()
        assert "browser_click" in offered(r, word)
    session["open"] = False
    r.next_turn()
    r.next_turn()
    r.next_turn()
    assert "browser_click" not in offered(r, "store")


def test_a_broken_live_check_never_breaks_selection():
    def boom():
        raise RuntimeError("x")

    assert "browser_click" not in offered(router(live=boom), "tell me a joke")


def test_only_using_a_toolkit_keeps_it_sticky_not_mentioning_it():
    # replaying 2026-10-11: sticky-on-mention raised the median tools offered from 31 to 41
    r = router()
    r.next_turn()
    assert "spotify_pause" in offered(r, "play some jazz")  # mentioned this turn: offered this turn
    r.next_turn()
    assert "spotify_pause" not in offered(r, "yes")  # nothing ran, so nothing to follow up on
    r.used("spotify_play")
    r.next_turn()
    assert "spotify_pause" in offered(r, "yes")


@pytest.mark.parametrize("text", [
    "check my gmail", "any new mail in gmail", "what's on my google calendar", "post this to slack",
    "open an issue on github", "add a row to my sheet", "ask composio to find my last invoice",
])
def test_apps_you_connected_through_mcp_are_offered_for_the_apps_they_serve(text):
    plugins = {**PLUGINS, "mcp": ["mcp_servers", "mcp_directory", "mcp_call"]}
    r = ToolkitRouter(lambda: plugins)
    assert "apps" in r.select(text)
    assert "mcp_call" in r.tool_names(text)  # not only the always-offered browser_task


def test_an_unrelated_request_does_not_pull_in_the_mcp_tools():
    plugins = {**PLUGINS, "mcp": ["mcp_servers", "mcp_directory", "mcp_call"]}
    r = ToolkitRouter(lambda: plugins)
    assert "mcp_call" not in r.tool_names("play some music") and "mcp_call" not in r.tool_names("tell me a joke")

