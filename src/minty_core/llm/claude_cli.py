"""Claude as a brain through your own Claude Code login: one `claude -p` call per model turn (no API key, no token
handling: the CLI signs itself in, as it does when you use it yourself).

What this module guarantees, because a model's answer must never become the program's power:
* the CLI runs with no tools, no settings, no MCP servers and no saved session, in an empty folder of its own, so it
  can only write text;
* the prompt (your conversation, memory, profile) goes in on stdin and the system prompt through a private
  file, never on the command line where `ps` shows it;
* ANTHROPIC_* variables are removed from its environment so it can never fall back to a billed API key;
* Minty's own tools are offered as text. A tool call is a line starting `<<CALL>>`; it is parsed as JSON and only
  names Minty actually offered this turn are returned, so the agent's safety gate decides, as for any model.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import AsyncIterator

from minty_core import osutil
from minty_core.config import ProviderConfig
from minty_core.llm.types import (Event, LLMError, Message, TextDelta, ToolCall, ToolCallEvent, ToolNotOffered,
                                  ToolSpec, Usage)

SENTINEL = "<<CALL>>"
ENV_KEEP = ("HOME", "PATH", "USER", "LOGNAME", "LANG", "LC_ALL", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
            "XDG_RUNTIME_DIR", "TMPDIR", "SHELL")
_LIMIT_TEXT = re.compile(r"usage limit|hit your limit|limit reached|out of (?:extra )?usage|rate.?limit|resets? ", re.I)
_AUTH_TEXT = re.compile(r"/login|invalid api key|not logged in|authenticat|oauth|credential", re.I)
MAX_COOLDOWN_S = 3600.0


def claude_command(cfg: ProviderConfig) -> str | None:
    """The `claude` program this provider would run, or None when it is not installed."""
    return shutil.which(str(cfg.extra.get("command") or "claude"))


def tool_lines(tools: list[ToolSpec]) -> str:
    out = []
    for t in tools:
        props = (t.parameters or {}).get("properties") or {}
        required = set((t.parameters or {}).get("required") or [])
        args = ", ".join(f"{k}{'' if k in required else '?'}: {(v or {}).get('type', 'any')}" for k, v in props.items())
        out.append(f"- {t.name}({args}): {t.description.strip()[:240]}")
    return "\n".join(out)


def system_text(messages: list[Message], tools: list[ToolSpec]) -> str:
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    if not tools:
        return system
    return (system + "\n\nTOOLS. You can use the tools below. To use one, reply with ONLY one line per call, exactly\n"
            f'{SENTINEL} {{"name": "<tool>", "arguments": {{...}}}}\n'
            "and nothing else. Otherwise answer in plain text. Never invent a tool that is not listed.\n" + tool_lines(tools))


def transcript(messages: list[Message]) -> str:
    lines = ["Conversation so far. Reply as the Assistant to the last message."]
    for m in messages:
        if m.role == "user":
            lines.append(f"User: {m.content}")
        elif m.role == "assistant":
            if m.content:
                lines.append(f"Assistant: {m.content}")
            for c in m.tool_calls:
                lines.append(f"Assistant: {SENTINEL} " + json.dumps({"name": c.name, "arguments": c.arguments}))
        elif m.role == "tool":
            lines.append(f"Tool result ({m.name or 'tool'}): {m.content}")
    return "\n".join(lines)


class _Splitter:
    """Text goes out as it arrives; from the first SENTINEL on, everything is held for parsing as tool calls."""

    def __init__(self) -> None:
        self.buffer, self.sent, self.calls_from = "", 0, -1

    def feed(self, text: str) -> str:
        self.buffer += text
        if self.calls_from < 0:
            self.calls_from = self.buffer.find(SENTINEL)
        limit = self.calls_from if self.calls_from >= 0 else max(self.sent, len(self.buffer) - (len(SENTINEL) - 1))
        out = self.buffer[self.sent:limit]
        self.sent = max(self.sent, limit)
        return out

    def finish(self) -> tuple[str, str]:
        """(remaining text, the part that is tool calls)."""
        if self.calls_from >= 0:
            return "", self.buffer[self.calls_from:]
        return self.buffer[self.sent:], ""


def parse_calls(tail: str, offered: set[str], provider: str) -> list[ToolCall]:
    calls = []
    for line in tail.splitlines():
        line = line.strip()
        if not line.startswith(SENTINEL):
            continue
        try:
            data = json.loads(line[len(SENTINEL):].strip())
            name, args = str(data["name"]), data.get("arguments") or {}
        except (ValueError, KeyError, TypeError) as e:
            raise LLMError(f"{provider}: malformed tool call", kind="other") from e
        if not isinstance(args, dict):
            raise LLMError(f"{provider}: malformed tool call", kind="other")
        if name not in offered:
            raise ToolNotOffered(f"{provider}: model tried to call tool '{name}' which is not in request.tools")
        calls.append(ToolCall(f"cc_{uuid.uuid4().hex[:12]}", name, args))
    return calls


def limit_error(name: str, info: dict | None, text: str = "") -> LLMError | None:
    """An LLMError (rate_limit) when the account's usage window is used up, else None."""
    info = info or {}
    status = str(info.get("status") or "")
    if status and status != "allowed" and not status.startswith("allowed"):
        resets = info.get("resetsAt")
        wait = min(max(float(resets) - time.time(), 60.0), MAX_COOLDOWN_S) if resets else 1800.0
        return LLMError(f"{name}: Claude usage limit reached ({info.get('rateLimitType') or 'window'})",
                        status=429, retry_after=wait, kind="rate_limit")
    if text and _LIMIT_TEXT.search(text):
        return LLMError(f"{name}: Claude usage limit reached", status=429, retry_after=1800.0, kind="rate_limit")
    return None


class ClaudeCliProvider:
    def __init__(self, cfg: ProviderConfig, workdir: Path | None = None):
        self.name = cfg.name
        self._model = cfg.model
        self._cfg = cfg
        self._command = str(cfg.extra.get("command") or "claude")
        self._effort = str(cfg.extra.get("effort") or "")
        self._reserve = float(cfg.extra.get("reserve", 0.9))  # stop using Claude when this much of a window is gone
        self._timeout = cfg.timeout_s
        self._workdir = Path(workdir) if workdir else Path(tempfile.gettempdir()) / "minty-claude-brain"
        self.last_usage: Usage | None = None

    # The router builds a provider per call, so what the account last reported is kept per account name here.
    _WINDOWS: dict[str, tuple[dict, float]] = {}

    @property
    def window(self) -> dict:
        """The account's usage windows as last reported: {"five_hour": 0.17, ...}."""
        return self._WINDOWS.get(self._cfg.name, ({}, 0.0))[0]

    @property
    def _resets_at(self) -> float:
        return self._WINDOWS.get(self._cfg.name, ({}, 0.0))[1]

    # -- what runs -------------------------------------------------------------------------------------
    def _argv(self, system_file: Path) -> list[str]:
        argv = [self._command, "-p", "--model", self._model, "--tools", "", "--setting-sources", "",
                "--strict-mcp-config", "--no-session-persistence", "--output-format", "stream-json", "--verbose",
                "--include-partial-messages", "--system-prompt-file", str(system_file)]
        if self._effort:
            argv += ["--effort", self._effort]
        return argv

    @staticmethod
    def _env() -> dict[str, str]:
        keep = ENV_KEEP + osutil.system_env_names()  # Windows: SystemRoot, USERPROFILE, APPDATA ... or the program cannot start or find its login
        return {k: v for k, v in os.environ.items() if k in keep and not k.startswith("ANTHROPIC")}

    def _spare(self) -> None:
        """Do not spend what is nearly gone: your own Claude Code use needs room (cfg.extra reserve, 0.9)."""
        if self._reserve <= 0 or time.time() >= self._resets_at:
            return
        worst = max(self.window.values(), default=0.0)
        if worst >= self._reserve:
            raise LLMError(f"{self.name}: Claude usage is at {worst:.0%}; keeping the rest for you", status=429,
                           retry_after=min(max(self._resets_at - time.time(), 60.0), MAX_COOLDOWN_S), kind="rate_limit")

    def _note_window(self, info: dict) -> None:
        wins = info.get("unifiedWindows") or {}
        self._WINDOWS[self._cfg.name] = (
            {k: float(v.get("utilization") or 0.0) for k, v in wins.items() if isinstance(v, dict)},
            max((float(v.get("resetsAt") or 0.0) for v in wins.values() if isinstance(v, dict)), default=0.0))

    # -- the call --------------------------------------------------------------------------------------
    async def stream(self, messages: list[Message], tools: list[ToolSpec]) -> AsyncIterator[Event]:
        self._spare()
        if shutil.which(self._command) is None:
            raise LLMError(f"{self.name}: the claude program is not installed", kind="other")
        self._workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(prefix="system-", suffix=".txt", dir=self._workdir)
        system_file = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as f:  # mkstemp makes it owner-only
            f.write(system_text(messages, tools))
        offered = {t.name for t in tools}
        splitter, result, info = _Splitter(), None, None
        proc = None
        try:
            try:
                argv = osutil.resolve_command(self._argv(system_file))
            except osutil.UnsafeCommand as e:
                raise LLMError(f"{self.name}: {e}", kind="other") from e
            proc = await asyncio.create_subprocess_exec(
                *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, cwd=str(self._workdir), env=self._env(),
                limit=1 << 20)
            proc.stdin.write(transcript(messages).encode("utf-8"))
            await proc.stdin.drain()
            proc.stdin.close()
            deadline = time.monotonic() + self._timeout
            while True:
                try:
                    raw = await asyncio.wait_for(proc.stdout.readline(), max(0.1, deadline - time.monotonic()))
                except asyncio.TimeoutError as e:
                    raise LLMError(f"{self.name}: no answer within {self._timeout:.0f}s", kind="timeout") from e
                if not raw:
                    break
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                kind = event.get("type")
                if kind == "stream_event":
                    delta = (event.get("event") or {}).get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        out = splitter.feed(delta["text"])
                        if out:
                            yield TextDelta(out)
                elif kind == "rate_limit_event":
                    info = event.get("rate_limit_info") or {}
                    self._note_window(info)
                elif kind == "result":
                    result = event
        finally:
            if proc is not None and proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()
            with contextlib.suppress(OSError):
                system_file.unlink()
        if result is None:
            raise limit_error(self.name, info) or LLMError(f"{self.name}: claude gave no answer", kind="server")
        text = str(result.get("result") or "")
        if result.get("is_error"):
            err = limit_error(self.name, info, text)
            if err:
                raise err
            if _AUTH_TEXT.search(text):
                raise LLMError(f"{self.name}: Claude is not signed in (run: claude)", status=401, kind="auth")
            raise LLMError(f"{self.name}: {text[:160] or 'claude reported an error'}", kind="other")
        self._record_usage(result.get("usage"))
        rest, tail = splitter.finish()
        if rest:
            yield TextDelta(rest)
        for call in parse_calls(tail, offered, self.name):
            yield ToolCallEvent(call)

    def _record_usage(self, u) -> None:
        if isinstance(u, dict):
            cached = int(u.get("cache_read_input_tokens") or 0)
            self.last_usage = Usage(tokens_in=int(u.get("input_tokens") or 0) + cached
                                    + int(u.get("cache_creation_input_tokens") or 0),
                                    tokens_out=int(u.get("output_tokens") or 0), cached=cached)
