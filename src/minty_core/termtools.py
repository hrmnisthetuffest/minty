"""Command-line tools this computer has, and a short hint for the model about them.

With the terminal on, a command-line tool often does a job faster and more reliably than clicking through a web page. The hint
names only the tools that are really installed here, says how to use each in one line, and rides along on a request only when
the request could use one (so ordinary chat costs nothing). It never changes what is allowed: a tool that is not on the terminal's
short read-only list is asked about each time, with the command read out.
"""
from __future__ import annotations

import re
import shutil
import time

# (program, what it is for, how to use it). Order is the order they are listed in.
TOOLS = (
    ("ddgr", "web search", "ddgr --json -n 5 'words' lists results (title, url, abstract); ddgr -j 'words' opens the top result "
                           "in the browser"),
    ("gh", "GitHub", "gh repo view, gh pr list, gh issue list"),
    ("git", "git", "git status, git log --oneline -5, git diff"),
    ("jq", "JSON", "jq . file.json"),
    ("rg", "searching inside files", "rg 'text' folder"),
    ("instagram-cli", "Instagram", "instagram-cli inbox / read / send / profile; the user logs in themselves "
                                    "(instagram-cli auth login), and anything that reads messages or sends is asked first"),
)
_REQUEST = re.compile(r"\b(search|look ?up|google|duckduckgo|ddg|web|open|browser|website|site|github|repo|repository|pull request|"
                      r"issues?|commits?|git|insta|instagram|dm|inbox|terminal|command|run|json|folder|files?)\b", re.I)
_CACHE: dict = {"at": 0.0, "found": []}
CACHE_S = 60.0


def found(which=shutil.which) -> list[tuple[str, str, str]]:
    """The tools above that are installed, in order."""
    return [t for t in TOOLS if which(t[0]) is not None]


def _installed() -> list[tuple[str, str, str]]:
    now = time.monotonic()
    if now - _CACHE["at"] > CACHE_S:
        _CACHE.update(at=now, found=found())
    return _CACHE["found"]


def hint(request: str, which=None) -> str:
    """Text for this request, or "" when it does not look like something a command-line tool could do."""
    text = request or ""
    tools = found(which) if which is not None else _installed()
    named = any(re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text, re.I) for name, _, _ in tools)  # whole words: "eight" is not "gh"
    if not tools or not (_REQUEST.search(text) or named):
        return ""
    lines = "; ".join(f"{name} ({what}: {how})" for name, what, how in tools)
    return ("Terminal: a command-line tool often does the job better than clicking around, so use shell_run when one fits. "
            f"Installed here: {lines}. To show the user a page, ddgr -j 'words' opens the top search result; xdg-open URL opens "
            "any other (asked first). Never type a password or a key into a command: the user logs in themselves. "
            "Anything not on the short read-only list is asked first.")
