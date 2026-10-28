"""Settings that decide what Minty may do without asking can be changed by the user (panel, API, config file) and by nothing the model
can call. A tool with one of these as a parameter would let text from a web page or an e-mail turn a safeguard off."""
import tempfile
from pathlib import Path

import minty_core.plugins.builtin as builtin
from minty_core import flags
from minty_core.context import build_context

PROTECTED = {
    "sandbox", "agent_env", "auto_start", "check_command", "claude_extra_tools", "claude_permission_mode", "claude_max_budget_usd",
    "auto_discover", "library_max_mb", "min_clips_per_style", "post_mode", "max_posts_per_day", "min_gap_hours", "blocked_words",
    "public_dir", "public_base_url", "ig_user_id", "ig_host", "ig_api_version", "project_dir", "preview_to_phone",
    "auto_reply_to", "approval_wait_s", "enabled", "settings",
}
PROTECTED_FLAG_PATHS = ("agents.", "studio.", "outbox.", "missions.", "recipes.", "plugins.")


def everything_on():
    ctx = build_context(Path(tempfile.mkdtemp()))
    data = ctx.store.config.model_dump(mode="json")
    data["plugins"] = {p.parent.name: {"enabled": True} for p in Path(builtin.__file__).parent.glob("*/plugin.py")}
    ctx.store.update(data)
    return ctx


def test_no_tool_the_model_can_call_takes_a_safeguard_as_a_parameter():
    ctx = everything_on()
    offenders = {}
    for spec in ctx.host.tool_specs():
        names = set((spec.parameters or {}).get("properties", {}))
        if names & PROTECTED:
            offenders[spec.name] = sorted(names & PROTECTED)
    assert not offenders, offenders


def test_the_switches_the_user_can_flip_by_voice_or_panel_do_not_include_these():
    ctx = everything_on()
    listed = [f["path"] if isinstance(f, dict) else str(f) for f in flags.list_flags(ctx.store.config)]
    assert listed  # there are switches, so the check below looks at something
    assert not [p for p in listed if p.startswith(PROTECTED_FLAG_PATHS)], listed


def test_the_services_plugins_share_give_no_way_to_write_settings():
    ctx = everything_on()
    for name, service in ctx.services.items():
        assert not any(w in name.lower() for w in ("set_config", "write_config", "update_config", "set_setting", "store_update")), name
