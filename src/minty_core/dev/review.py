"""Branch review through Claude Code in headless mode (651). This only BUILDS the call: plan mode (cannot edit), read tools
and `git diff/log/show` only. Running it uses your Claude plan and sends the diff to Anthropic, so the tool asks first."""
from __future__ import annotations

import re
from pathlib import Path

from minty_core.plugins.builtin.agents.adapters import ClaudeCodeAdapter, Invocation

_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,79}")
TOOLS = ["Read", "Grep", "Glob", "Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)"]


def _ref(value: str) -> str:
    value = str(value or "").strip()
    if not _REF.fullmatch(value) or ".." in value or value.endswith(("/", ".lock")):
        raise ValueError("That is not a plain branch or commit name.")
    return value


def build_review(project: Path, base: str, head: str) -> Invocation:
    base, head = _ref(base), _ref(head)
    prompt = (f"Review the changes on {head} compared with {base}. Look only at `git diff {base}...{head}` and "
              f"`git log {base}..{head}`, and read files for context. Find bugs, security problems, missing tests and "
              "confusing code. Reply with at most 8 short bullet points, most serious first, each naming the file. "
              "Do not change anything and do not run anything else.")
    return ClaudeCodeAdapter(permission_mode="plan", allowed_tools=TOOLS).build(Path(project), prompt, None)
