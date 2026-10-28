"""Tool descriptions stay short (W0-29): every tool's schema is sent with every request that offers it."""
from pathlib import Path

import pytest

import minty_core.plugins.builtin as builtin
from tests.plugin_helpers import make_host

ALL = {p.parent.name: {"enabled": True} for p in Path(builtin.__file__).parent.glob("*/plugin.py")}
MAX_DESCRIPTION = 340
MAX_TOTAL_DESCRIPTIONS = 25500  # +7 playbooks tools (~700; in the habits kit, offered only when asked); +6 prefs tools and notices_feedback (~800; own toolkits, offered only when asked); +3 notices tools (~300; own toolkit, offered only when asked about notices); +4 artifacts tools (~470; in the apps kit, offered when asked for a sheet or doc or when MCP is set up); +4 studio tools (~950; only offered with the studio kit); +4 recipe tools (~700; in the recipes kit); +missions_review (~140, in the missions kit); +3 attention tools (~330; in the attention kit); +4 outbox tools (~410; only offered with the mail kit); +4 mission tools and start_task's mode (~390; missions_start is a core tool, the rest are in the missions kit); +1 shell tool (the terminal, ~255); was 10 431 for 122 tools before W0-29 (9 386 after); +7 health tools (WP-22, ~380) +5 coach (WP-23, ~270) +8 finance (WP-24, ~560) +4 planning (WP-28, ~330) +3 nutrition (WP-29, ~420) +2 money insight (WP-30, ~250) +5 life (WP-25, ~400) +16 vault (WP-12, ~1 100) +7 butler (WP-26, ~420) +2 butler sort/apply +1 mail triage (~250) +5 memory governance (WP-13, ~420) +2 forget (~220) +3 private notes (~260) +13 study (WP-14, ~950; toolkit routing offers only the study kit when asked); +8 productivity (WP-15, ~540; toolkit routing offers only the productivity kit when asked); +9 web helpers (WP-16, ~1 060; toolkit routing offers only the web kit when asked); +6 system tools (WP-17, ~850; toolkit routing offers only the system kit when asked); +7 dev tools (WP-18, ~690; toolkit routing offers only the developer kit when asked); +9 create tools (WP-19, ~1 010; toolkit routing offers only the creator kit when asked); +6 integrations +3 mcp tools (WP-20, ~820; toolkit routing offers only the world kit when asked); +2 selfcheck tools (WP-21, ~170; toolkit routing offers only the selfcheck kit when asked)


@pytest.fixture(scope="module")
def specs(tmp_path_factory):
    return {s.name: s for s in make_host(tmp_path_factory.mktemp("cfg"), plugins=ALL).tool_specs()}


def test_no_tool_description_is_long(specs):
    long = {n: len(s.description) for n, s in specs.items() if len(s.description) > MAX_DESCRIPTION}
    assert not long


def test_descriptions_together_stay_within_budget(specs):
    assert sum(len(s.description) for s in specs.values()) <= MAX_TOTAL_DESCRIPTIONS


@pytest.mark.parametrize("tool, phrase", [
    ("browser_task", "before buying, sending, posting or deleting"),
    ("browser_profile_set", "Never passwords or card numbers"),
    ("browser_type", "Never for passwords or card data"),
    ("n8n_create_workflow", "INACTIVE"),
    ("apis_call", "untrusted"),
    ("research_start", "Only after the user asked"),
    ("research_blueprint", "Only when asked"),
    ("deep_think_think_in_background", "Never for simple questions"),
    ("spotify_play", "never swap in a playlist name"),
])
def test_safety_and_usage_rules_survive_the_shortening(specs, tool, phrase):
    assert phrase in specs[tool].description
