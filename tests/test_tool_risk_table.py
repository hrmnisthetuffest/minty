"""Every tool's risk class is deliberate: the full table is a snapshot, so a new tool or a changed class shows up
in review. After a deliberate change: MINTY_SNAPSHOT=1 pytest tests/test_tool_risk_table.py"""
import os
from pathlib import Path

import pytest

import minty_core.plugins.builtin as builtin
from minty_core.context import build_context
from minty_core.plugins.host import FilteredHost
from minty_core.policy import R0, R1, R2, R3, RISK_NAMES, classify

SNAPSHOT = Path(__file__).parent / "snapshots" / "tool_risk.txt"
PLUGINS = sorted(p.parent.name for p in Path(builtin.__file__).parent.glob("*/plugin.py"))


@pytest.fixture
def host(tmp_path):
    ctx = build_context(tmp_path)
    data = ctx.store.config.model_dump(mode="json")
    data["plugins"] = {name: {"enabled": True} for name in PLUGINS}
    ctx.store.update(data)
    return ctx.host


def row(name, tool) -> str:
    base = classify(tool, {})
    flags = [f for f, on in (("+args", callable(tool.risk) or tool.confirm_when is not None),
                             ("egress", tool.egress is not None), ("sensitive", tool.sensitive),
                             ("local-only", tool.local_only)) if on]
    return f"{name:34} {base} {RISK_NAMES[base]:8} {' '.join(flags)}".rstrip()


def test_every_tool_has_a_reviewed_risk_class(host):
    rows = [row(s.name, host.get_tool(s.name)) for s in sorted(host.tool_specs(), key=lambda s: s.name)]
    loaded = {r.split()[0] for r in rows}
    assert all(any(n.startswith(p + "_") for n in loaded) for p in PLUGINS if p != "__pycache__"), "a plugin failed"
    text = "\n".join(rows) + "\n"
    if os.environ.get("MINTY_SNAPSHOT"):
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(text)
    assert text == SNAPSHOT.read_text(), "tool risk table changed: review it, then refresh with MINTY_SNAPSHOT=1"


def test_classes_that_depend_on_the_arguments(host):
    def cls(name, args):
        return classify(host.get_tool(name), args)

    assert cls("browser_press", {"key": "Enter"}) == R2 and cls("browser_press", {"key": "PageDown"}) == R0
    assert cls("files_write", {"overwrite": True}) == R2 and cls("files_write", {}) == R1
    assert cls("apis_send", {"method": "DELETE"}) == R3 and cls("apis_send", {"method": "POST"}) == R2
    leaky = "https://x.example/track?email=me@home.example"
    for name in ("browser_open", "files_download", "apis_read_url", "display_open_url"):
        egress = host.get_tool(name).egress
        assert egress({"url": leaky}) == leaky and egress({"url": "https://x.example/docs"}) is None


def test_helpers_never_see_tools_that_are_always_asked(host):
    helper = FilteredHost(host)
    assert helper.get_tool("prompts_add_instruction") is None and helper.get_tool("smart_home_control_secure") is None
    assert helper.get_tool("weather_get_weather") is not None and helper.get_tool("prompts_learn") is not None
    assert FilteredHost(host, allow_risky=True).get_tool("prompts_add_instruction") is not None
