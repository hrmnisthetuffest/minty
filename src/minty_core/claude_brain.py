"""`minty claude-brain on|off|status`: switch Claude (through your own Claude Code login) on as the main brain.

On: the ladder; when the account's usage window is used up
(or nearly, see `reserve`) the free providers take over by themselves and Claude rests until it resets.
"""
from __future__ import annotations

from minty_core.config import CLAUDE_BRAIN, CLAUDE_BRAIN_LEGACY, ConfigStore, claude_brain_providers

MIN_DEADLINE_S = 12.0


def claude_brain(action: str, store: ConfigStore, *, installed: bool | None = None) -> str:
    if installed is None:
        import shutil

        installed = shutil.which("claude") is not None
    data = store.config.model_dump(mode="json")
    others = [p for p in data["providers"] if p["name"] not in CLAUDE_BRAIN + CLAUDE_BRAIN_LEGACY]
    on = len(others) != len(data["providers"])
    if action == "status":
        mine = [p for p in store.config.providers if p.name in CLAUDE_BRAIN]
        state = "on" if on else "off"
        models = ("haiku low (simple), haiku high (medium), sonnet low (hard), sonnet high (thinking mode); "
                  "opus only when you say \"use opus\"") if mine else ""
        return (f"Claude brain is {state}. The claude program is {'installed' if installed else 'not installed'}."
                + (f" {models}. Kept back for you: {float(mine[0].extra.get('reserve', 0.9)):.0%} of a usage window."
                   if mine else ""))
    if action == "off":
        data["providers"] = others
        if data.get("preferred_provider") == "claude":
            data["preferred_provider"] = ""
        store.update(data)
        return "Claude brain is off. The other providers are as they were."
    if not installed:
        return ("The claude program is not installed, so nothing changed. Install it with: "
                "curl -fsSL https://claude.ai/install.sh | bash, then run: claude")
    mine = [p.model_dump(mode="json") for p in claude_brain_providers()]
    data["providers"] = mine + others
    data["preferred_provider"] = "claude"
    # each answer starts a Claude Code process: 2 to 5 s normally, sometimes 10; the free providers wait behind it
    data["turn"]["first_token_deadline_s"] = max(float(data["turn"]["first_token_deadline_s"]), MIN_DEADLINE_S)
    store.update(data)
    return ("Claude brain is on: Haiku low for most turns, Haiku high for medium ones, Sonnet low for hard ones, Sonnet "
            "high for thinking mode; Opus only when you say \"use opus\" (and at the effort you name). When your Claude "
            "usage runs out the free providers take over. Restart Minty to apply.")
