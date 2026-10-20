"""Read-only git. Every call starts with a fixed safe configuration (no file-system monitor, no pager, no external diff, no
hooks) so a repository's own settings cannot run a program, and only reading sub-commands are accepted by the runner."""
from __future__ import annotations

from minty_core.system.runner import Result, Runner

ALLOWED_C = {"core.fsmonitor=false", "core.pager=cat", "protocol.ext.allow=never", "diff.external=",
             "core.hooksPath=/dev/null"}
SAFE_PREFIX = [x for kv in sorted(ALLOWED_C) for x in ("-c", kv)]
READ = {"log", "status", "diff", "rev-parse", "remote", "shortlog", "show", "ls-files", "describe", "rev-list",
        "for-each-ref", "branch"}
BANNED = ("--output", "--ext-diff", "--exec", "-O", "--open-files-in-pager", "--upload-pack", "--receive-pack",
          "--git-dir", "--work-tree", "--textconv", "--no-index")
BRANCH_OK = {"--list", "-a", "-r", "-v", "-vv", "--show-current", "--all", "--remotes"}


def git_rule(a: list[str]) -> bool:
    i = 1
    while i < len(a) and a[i] == "-c":
        if i + 1 >= len(a) or a[i + 1] not in ALLOWED_C:
            return False
        i += 2
    if i >= len(a) or a[i] not in READ:
        return False
    sub, rest = a[i], a[i + 1:]
    if any(x.startswith(BANNED) for x in rest):
        return False
    if sub == "branch":
        return all(x in BRANCH_OK for x in rest)
    if sub == "remote":
        return rest in ([], ["-v"]) or (len(rest) == 2 and rest[0] == "get-url" and not rest[1].startswith("-"))
    return True


def make_runner(timeout: float = 20.0) -> Runner:
    return Runner(allowed={"git"}, rules={"git": git_rule}, timeout=timeout)


class Git:
    def __init__(self, runner):
        self.r = runner

    async def run(self, cwd, *args: str) -> Result:
        return await self.r.run(["git", *SAFE_PREFIX, *args], cwd=str(cwd))
