"""The tool menu (`need_toolkit`) is sent with every request. It names every group and says in a few words what it holds: a long
description there costs every question, so it is kept short and nothing is lost that the router's keywords do not already hold."""
import json
import tempfile
from pathlib import Path

from minty_core import toolkits
from minty_core.context import build_context
import minty_core.plugins.builtin as builtin

MENU_MAX_CHARS = 2000  # was 3 258 with 37 groups
BLURB_MAX_CHARS = 64


def menu(request="what is the capital of France"):
    ctx = build_context(Path(tempfile.mkdtemp()))
    data = ctx.store.config.model_dump(mode="json")
    data["plugins"] = {p.parent.name: {"enabled": True} for p in Path(builtin.__file__).parent.glob("*/plugin.py")}
    ctx.store.update(data)
    return next(s for s in ctx.agent._tool_specs(request) if s.name == toolkits.NEED_TOOLKIT)


def test_the_menu_is_short_and_still_names_every_group():
    spec = menu()
    assert len(spec.description) <= MENU_MAX_CHARS, len(spec.description)
    names = spec.parameters["properties"]["name"]["enum"]
    assert len(names) >= 30
    for name in names:
        assert name in spec.description or name in ("deep_think", "life", "scenes", "timers"), name


def test_every_blurb_is_a_few_words():
    long = {k: len(v[1]) for k, v in toolkits.KITS.items() if len(v[1]) > BLURB_MAX_CHARS}
    assert not long, long


def test_the_groups_a_request_needs_are_still_chosen_by_keywords_not_by_the_menu_text():
    router_text = {k: v[2] for k, v in toolkits.KITS.items()}
    assert "reel" in router_text["studio"] and "instagram" in router_text["studio"]
    assert "email" in router_text["mail"] and "schedule" in router_text["recipes"]
