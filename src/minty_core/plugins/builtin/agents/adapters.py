import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)
_AGENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
DEFAULT_TOOLS = ["Read", "Edit", "Write", "Glob", "Grep", "Bash(git:*)", "Bash(python:*)", "Bash(python3:*)",
                 "Bash(pip:*)", "Bash(pytest:*)", "Bash(npm:*)", "Bash(node:*)", "Bash(ls:*)", "Bash(mkdir:*)",
                 "Bash(ffmpeg:*)", "Bash(ffprobe:*)"]  # ffmpeg so a coding agent can render and check video
# a deny rule beats an allow rule: the job brief says "never push or rewrite history"; these make the tool say it too.
# (Guard rails for an honest agent, not a sandbox: `python` can still do anything a script can.)
DENIED_TOOLS = ["Bash(git push:*)", "Bash(git reset:*)", "Bash(git clean:*)", "Bash(git rebase:*)",
                "Bash(git remote:*)", "Bash(git config:*)", "Bash(git filter-branch:*)"]
READ_ONLY_TOOLS = ["Read", "Glob", "Grep"]
# programs that are never added through the extra-tools setting, and the shapes that would allow everything
NEVER_EXTRA = ("rm", "sudo", "su", "doas", "dd", "mkfs", "chmod", "chown", "curl", "wget", "ssh", "scp", "nc", "ncat", "eval", "bash", "sh")
_TOOL_NAME = re.compile(r"^[A-Za-z]+(?:\([A-Za-z0-9_.:*/~ -]{1,60}\))?$")

# What a coding agent is started with: enough to run and to sign in, never Minty's provider keys, bot tokens or SSH agent.
ENV_PASS = frozenset({"PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM", "TMPDIR",
                      "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME",
                      "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE",
                      "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "no_proxy", "all_proxy"})
ENV_PREFIXES = ("ANTHROPIC_", "CLAUDE_")  # Claude Code's own sign-in, model and settings variables
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,63}")


def job_env(environ=None, extra="") -> dict[str, str]:
    """The environment for a coding agent: the allowlist above, plus any names the user listed in the `claude_env` setting."""
    environ = os.environ if environ is None else environ
    wanted = {n for n in re.split(r"[,;\s]+", str(extra or "")) if _ENV_NAME.fullmatch(n)}
    return {k: v for k, v in environ.items() if k in ENV_PASS or k in wanted or k.startswith(ENV_PREFIXES)}


# Folders and files an agent has no business reading or writing. A guard rail for the agent's own Read/Edit/Write tools; the
# sandbox (isolation.py) is what keeps a script it writes away from them.
_SECRET_DIRS = (".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".password-store", ".config/gcloud", ".config/gh",
                ".config/google-chrome", ".config/chromium", ".mozilla", ".config/Bitwarden")
_SECRET_FILES = (".netrc", ".git-credentials", ".npmrc", ".pypirc", ".claude/.credentials.json")


def secret_path_rules(home, config_dir) -> list[str]:
    """Claude Code deny rules (`//path` is an absolute path) for the places keys live, including Minty's own folder."""
    home, config_dir = Path(home), Path(config_dir)
    dirs = [home / d for d in _SECRET_DIRS] + [config_dir]
    try:
        real = config_dir.resolve()
        if real != config_dir:
            dirs.append(real)  # the settings folder is often a link to somewhere else
    except OSError:
        pass
    paths = [f"/{d}/**" for d in dirs] + [f"/{home / f}" for f in _SECRET_FILES]
    return [f"{tool}({p})" for p in paths for tool in ("Read", "Edit")]


def extra_tools(text) -> tuple[list[str], list[str]]:
    """(accepted, refused) from a setting like "Bash(npx:*) Bash(ffprobe:*)". A blanket allow, or a program that can delete,
    escalate or fetch from the network, is refused."""
    accepted, refused = [], []
    for item in re.split(r"[,\s]+(?![^()]*\))", str(text or "")):
        item = item.strip()
        if not item:
            continue
        program = re.match(r"^Bash\(([A-Za-z0-9_.-]*)", item)
        blanket = item == "Bash" or item.startswith(("Bash(*", "Bash(:")) or (program is not None and not program.group(1))
        if not _TOOL_NAME.match(item) or blanket or (program and program.group(1) in NEVER_EXTRA):
            refused.append(item)
        elif item not in accepted:
            accepted.append(item)
    return accepted, refused


@dataclass
class Invocation:
    argv: list[str]
    cwd: Path


@dataclass
class Parsed:
    summary: str
    session_id: str | None = None
    failed: bool = False


class Adapter(Protocol):
    name: str
    binary: str
    readonly_capable: bool  # can be told to look and not touch (build(..., readonly=True)); absent means no

    def build(self, project: Path, prompt: str, session_id: str | None) -> Invocation: ...
    def parse(self, stdout: str) -> Parsed: ...


def tail(text: str, limit: int = 400) -> str:
    text = " ".join(text.split())
    return text[-limit:]


def _load_json(text: str) -> dict | None:
    """Parse a JSON object from agent output; stderr is merged in, so tolerate log lines around it."""
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    lines = text.splitlines(keepends=True)
    starts, pos = [], 0
    for line in lines:
        if line.startswith("{"):
            starts.append(pos)
        pos += len(line)
    decoder = json.JSONDecoder()
    for start in reversed(starts):
        try:
            data, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


class ClaudeCodeAdapter:
    name = "claude_code"
    binary = "claude"
    readonly_capable = True
    sandboxable = True
    env_dirs = ("CLAUDE_CONFIG_DIR",)  # a folder named by one of these variables is as much its own as ~/.claude
    home_rw = (".claude", ".claude.json")  # its sign-in and session state: the only parts of the home folder it may change
    home_ro = (".gitconfig", ".config/git", ".local/bin", ".local/lib", ".local/share/claude")  # who "I" am to git, and its own programs

    def __init__(self, permission_mode: str = "acceptEdits", allowed_tools: list[str] | None = None,
                 max_budget_usd: float | None = None, extra: list[str] | None = None, secret_rules: list[str] | None = None):
        self._deny = DENIED_TOOLS + list(secret_rules or [])
        self._mode = permission_mode
        self._tools = list(DEFAULT_TOOLS if allowed_tools is None else allowed_tools) + [t for t in (extra or []) if t not in (allowed_tools or DEFAULT_TOOLS)]
        self._budget = max_budget_usd if max_budget_usd and max_budget_usd > 0 else None

    def build(self, project: Path, prompt: str, session_id: str | None, readonly: bool = False) -> Invocation:
        argv = ["claude", "-p", prompt, "--output-format", "json"]
        if session_id:
            argv += ["--resume", session_id]
        if self._budget:
            argv += ["--max-budget-usd", f"{self._budget:g}"]
        mode, tools = ("plan", READ_ONLY_TOOLS) if readonly else (self._mode, self._tools)
        argv += ["--permission-mode", mode, "--disallowedTools", *self._deny, "--allowedTools", *tools]
        return Invocation(argv, project)

    def parse(self, stdout: str) -> Parsed:
        data = _load_json(stdout)
        if data is None:
            return Parsed(tail(stdout))
        return Parsed(tail(str(data.get("result", "")), 1000) or tail(stdout), data.get("session_id"),
                      failed=bool(data.get("is_error")))


class HermesAdapter:
    name = "hermes"
    binary = "hermes"

    def build(self, project: Path, prompt: str, session_id: str | None) -> Invocation:
        return Invocation(["hermes", "-z", prompt], project)

    def parse(self, stdout: str) -> Parsed:
        summary = tail(stdout, 1000)
        return Parsed(summary, failed=not summary)


class OpenClawAdapter:
    name = "openclaw"
    binary = "openclaw"
    _KEYS = ("reply", "text", "message", "result", "output")

    def __init__(self, agent_id: str = "main"):
        agent_id = str(agent_id).strip()
        if agent_id and not _AGENT_ID.fullmatch(agent_id):
            log.warning("Ignoring invalid openclaw agent id %r; using main", agent_id)
            agent_id = ""
        self._agent_id = agent_id or "main"

    def build(self, project: Path, prompt: str, session_id: str | None) -> Invocation:
        return Invocation(["openclaw", "agent", "--agent", self._agent_id, "--message", prompt, "--json"], project)

    @staticmethod
    def _error_text(error) -> str:
        if isinstance(error, dict) and isinstance(error.get("message"), str) and error["message"].strip():
            return error["message"]
        return error if isinstance(error, str) else json.dumps(error)

    def parse(self, stdout: str) -> Parsed:
        if not stdout.strip():
            return Parsed("", failed=True)
        data = _load_json(stdout)
        if data is None:
            return Parsed(tail(stdout, 1000))
        error = data.get("error")
        failed = bool(error) or data.get("ok") is False
        if error:
            return Parsed(tail(self._error_text(error), 1000), failed=True)
        for key in self._KEYS:
            if isinstance(data.get(key), str) and data[key].strip():
                return Parsed(tail(data[key], 1000), failed=failed)
        return Parsed(tail(stdout, 1000), failed=failed)
