"""The terminal's engine: what may run without asking, what is refused outright, and how a command runs.

Only a handful of read-only commands run without a question (`safe_argv`): each is checked word by word, run without a shell,
and a single metacharacter anywhere sends the whole thing back to "ask first". Everything else is for the user to approve, with
the exact command read out; that approval is the real boundary. The refusal of `sudo` and friends is a convenience for the
common case, not protection: a command can always be written another way, which is why nothing runs unapproved.

A command runs in its own process group with no keys in its environment, no input, a time limit and a cap on its output. The
whole group is killed on a timeout and when the turn that started it is cancelled (speaking over Minty cancels a turn).
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shlex
import shutil
import signal
from dataclasses import dataclass

MAX_COMMAND_CHARS = 2000
DEFAULT_MAX_CHARS = 8000
HARD_BYTES = 2_000_000  # more output than this is not read: the command is stopped instead of filling memory
_METACHARS = frozenset(";&|<>$`\n\r\x00")
_FLAGS = re.compile(r"^-[A-Za-z]+$")
_PLAIN = re.compile(r"^[\w.+@%^~/:=,-]+$")  # a ref, a path or a program name: no quotes, no glob, no leading dash
_LOG_FLAGS = frozenset({"--oneline", "--graph", "--stat", "--decorate", "--all", "--name-only", "--name-status", "--shortstat",
                        "--no-color", "-p", "--cached", "--staged", "--summary", "--abbrev-commit", "--no-merges"})
_STATUS_FLAGS = frozenset({"-s", "-b", "-sb", "--short", "--branch", "--porcelain", "--long"})
_BRANCH_FLAGS = frozenset({"-a", "-r", "-v", "-vv", "--list", "--all", "--remotes", "--show-current", "--verbose"})
_GIT_HEAD = ["git", "--no-pager", "-c", "core.fsmonitor=false"]  # a repository's own settings must not be able to run code
_PRIVILEGE = re.compile(r"(?:^|[\s;&|(`$])(?:sudo|su|doas|pkexec|run0)(?=$|[\s;&|)`])")
_PASS_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "USER", "LOGNAME", "TZ")
# only for a command that opens something on the screen (the browser): where the screen is, never a key or a token
_SCREEN_ENV = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "XDG_CURRENT_DESKTOP",
               "XDG_SESSION_TYPE", "BROWSER")
_DDGR_MODES = {"--json": "read", "-j": "open", "--ducky": "open"}
_DDGR_VALUED = {"-w": r"[A-Za-z0-9.-]{1,80}", "--site": r"[A-Za-z0-9.-]{1,80}", "-t": r"[dwmy]", "--time": r"[dwmy]",
                "-r": r"[a-z]{2}-[a-z]{2}", "--reg": r"[a-z]{2}-[a-z]{2}"}
MAX_QUERY_CHARS = 300


def _args_ok(program: str, args: list[str]) -> bool:
    if program in ("pwd", "whoami", "hostname"):
        return not args
    if program == "date":  # never -s / --set
        return all(a in ("-u", "--utc") or (a.startswith("+") and len(a) > 1) for a in args) and sum(a.startswith("+") for a in args) <= 1
    if program == "uptime":
        return all(a in ("-p", "-s", "--pretty", "--since") for a in args)
    if program in ("uname", "free"):
        return all(_FLAGS.match(a) for a in args)
    if program == "df":
        return all(_FLAGS.match(a) or _PLAIN.match(a) for a in args)
    if program == "ls":
        return all(_FLAGS.match(a) or not a.startswith("-") for a in args)
    if program == "which":
        return bool(args) and all(a == "-a" or _PLAIN.match(a) for a in args)
    return False


def _git(args: list[str]) -> list[str] | None:
    if not args:
        return None
    sub, rest = args[0], args[1:]
    if sub == "status":
        return _GIT_HEAD + ["status", *rest] if all(a in _STATUS_FLAGS for a in rest) else None
    if sub == "branch":
        return _GIT_HEAD + ["branch", *rest] if all(a in _BRANCH_FLAGS for a in rest) else None
    if sub in ("log", "diff"):
        ok = all(a in _LOG_FLAGS or re.fullmatch(r"-\d{1,4}", a) or (not a.startswith("-") and _PLAIN.match(a)) for a in rest)
        extra = ["-c", "log.showSignature=false"] if sub == "log" else []  # a repository's setting must not run a signature program
        return _GIT_HEAD + extra + [sub, "--no-ext-diff", "--no-textconv", *rest] if ok else None
    return None


def _ddgr(args: list[str]) -> list[str] | None:
    """A web search from the terminal: exactly one of --json (print the results) or -j (open the top one in the browser), a few
    harmless options, and the words to search for as plain arguments. Anything else (no mode, a proxy, unsafe search ...) is asked."""
    mode, options, words, i = None, [], [], 0
    while i < len(args):
        a = args[i]
        if a in _DDGR_MODES:
            if mode is not None:
                return None
            mode, i = a, i + 1
        elif a in ("-n", "--num"):
            if i + 1 >= len(args) or not re.fullmatch(r"\d{1,2}", args[i + 1]) or not 1 <= int(args[i + 1]) <= 25:
                return None
            options += [a, args[i + 1]]
            i += 2
        elif a in _DDGR_VALUED:
            if i + 1 >= len(args) or not re.fullmatch(_DDGR_VALUED[a], args[i + 1]):
                return None
            options += [a, args[i + 1]]
            i += 2
        elif a.startswith("-"):
            return None  # a query word can never be read as an option
        else:
            words.append(a)
            i += 1
    if mode is None or not words or sum(map(len, words)) > MAX_QUERY_CHARS:
        return None
    return ["ddgr", mode, *options, *words]


def has_metachars(command: str) -> bool:
    """True when a command line contains anything a shell would treat specially (so it cannot be split safely)."""
    return bool(_METACHARS & set(command))


def safe_argv(command: str) -> list[str] | None:
    """The exact argument list for a command that may run without asking, or None (then it must be approved)."""
    if not isinstance(command, str) or len(command) > MAX_COMMAND_CHARS or _METACHARS & set(command):
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if not words:
        return None
    program, args = words[0], words[1:]
    if program == "git":
        return _git(args)
    if program == "ddgr":
        return _ddgr(args)
    if program in ("pwd", "ls", "whoami", "date", "uname", "uptime", "df", "free", "hostname", "which") and _args_ok(program, args):
        return [program, *args]
    return None


def classify_command(command: str) -> int:
    """Risk class: 0 for the few read-only commands above, 1 for opening a search result in the browser (a reversible change,
    asked in the careful mode), 3 for everything else (always asked, whatever the settings)."""
    argv = safe_argv(command)
    if argv is None:
        return 3
    return 1 if needs_display(command) else 0


def uses_network(command: str) -> bool:
    """A command on the list that sends something over the internet (a search): privacy mode refuses it."""
    argv = safe_argv(command)
    return bool(argv) and argv[0] == "ddgr"


def needs_display(command: str) -> bool:
    """A command on the list that opens something on the screen (the top search result in the browser)."""
    argv = safe_argv(command)
    return bool(argv) and argv[0] == "ddgr" and argv[1] in ("-j", "--ducky")


def blocked_reason(command: str) -> str | None:
    """A reason (spoken as it is) why this command is not run at all, or None. Checked before anyone is asked."""
    if not isinstance(command, str) or not command.strip():
        return "Tell me which command to run."
    if len(command) > MAX_COMMAND_CHARS:
        return f"That command is too long (over {MAX_COMMAND_CHARS} characters); run it yourself or split it up."
    if "\x00" in command:
        return "That command has a character I cannot run."
    if _PRIVILEGE.search(command):
        return "I don't run commands as another user (sudo, su). Run that one yourself."
    return None


def sandbox_env(display: bool = False) -> dict[str, str]:
    """A plain environment: no API keys or tokens from the parent, a terminal that is not a terminal. With `display`, also where
    the screen and the browser are, so a command can open a page."""
    env = {k: os.environ[k] for k in _PASS_ENV + (_SCREEN_ENV if display else ()) if k in os.environ}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.update(TERM="dumb", PAGER="cat", GIT_TERMINAL_PROMPT="0", NO_COLOR="1")
    return env


@dataclass
class Result:
    code: int
    text: str = ""
    timed_out: bool = False
    truncated: bool = False
    too_much: bool = False  # it kept writing past the hard limit and was stopped
    missing: bool = False


class _Output:
    """The first and last part of what a command wrote; the middle of a long output is counted, not kept."""

    def __init__(self, max_chars: int):
        self.head_n = self.tail_n = max(50, max_chars // 2)
        self.head, self.tail, self.total = b"", b"", 0

    def add(self, chunk: bytes) -> None:
        self.total += len(chunk)
        room = self.head_n - len(self.head)
        if room > 0:
            self.head += chunk[:room]
            chunk = chunk[room:]
        if chunk:
            self.tail = (self.tail + chunk)[-self.tail_n:]

    def render(self) -> tuple[str, bool]:
        cut = self.total - len(self.head) - len(self.tail)
        head, tail = self.head.decode("utf-8", "replace"), self.tail.decode("utf-8", "replace")
        if cut > 0:
            return f"{head}\n[… {cut} characters cut …]\n{tail}", True
        return head + tail, False


def _kill_group(proc) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)  # the command was started as the head of its own group, so this is all of it


async def _drain(proc, out: _Output, hard_bytes: int) -> bool:
    """Read until the command closes its output; False when it was cut off for writing too much."""
    while True:
        chunk = await proc.stdout.read(65536)
        if not chunk:
            await proc.wait()
            return True
        out.add(chunk)
        if out.total > hard_bytes:
            return False


async def _discard(stream) -> None:
    while await stream.read(65536):
        pass


async def run(command, *, cwd=None, timeout_s: float = 30.0, max_chars: int = DEFAULT_MAX_CHARS,
              hard_bytes: int = HARD_BYTES, display: bool = False) -> Result:
    """Run a command line (a string, through bash) or an exact argument list (a list, no shell)."""
    env = sandbox_env(display)
    if isinstance(command, str):
        argv = [shutil.which("bash", path=env["PATH"]) or "/bin/sh", "-c", command]
    else:
        argv = list(command)
        found = shutil.which(argv[0], path=env["PATH"])
        if found is None:
            return Result(127, f"{argv[0]} is not installed", missing=True)
        argv[0] = found
    proc = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT, cwd=cwd, env=env, start_new_session=True)
    out, timed_out, complete = _Output(max_chars), False, True
    try:
        try:
            complete = await asyncio.wait_for(_drain(proc, out, hard_bytes), timeout_s)
        except asyncio.TimeoutError:
            timed_out = True
    except asyncio.CancelledError:  # the turn was cancelled (someone spoke over Minty): nothing it started may carry on
        _kill_group(proc)
        raise
    finally:
        if proc.returncode is None or timed_out or not complete:
            _kill_group(proc)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), 2)
            with contextlib.suppress(Exception):  # whatever it had already written is thrown away so its pipe can close
                await asyncio.wait_for(_discard(proc.stdout), 2)
    text, truncated = out.render()
    code = 124 if timed_out else (proc.returncode if proc.returncode is not None else -9)
    return Result(code, text, timed_out=timed_out, truncated=truncated, too_much=not complete)
