"""`minty mcp ...` at the terminal: add, look at, allow, revoke and remove MCP servers and tools. Only you, here, can do any
of these: no tool a model can call touches this file. Looking at a server's tools starts it; allowing one is a question to you."""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Callable

from minty_core.mcp.client import McpError, open_session
from minty_core.mcp.directory import DIRECTORY, REMOTE, command_for, find
from minty_core.mcp.registry import McpRegistry, sanitize_description, tool_hash
from urllib.parse import urlsplit

USAGE = """minty mcp list
minty mcp directory
minty mcp add NAME [--from-directory ENTRY] [--folder PATH] [--env KEY]... [-- COMMAND ARGS...]
minty mcp add-remote NAME HTTPS_URL [--header HEADER=SECRET_NAME]...
minty mcp composio NAME --user-id ID
minty mcp tools NAME
minty mcp allow NAME TOOL [--readonly] [--yes]
minty mcp revoke NAME TOOL
minty mcp remove NAME
Set a secret a server needs with: minty set-key plugin.mcp.NAME.KEY"""


async def _tools(reg: McpRegistry, name: str, secret, timeout: float = 30.0) -> list[dict]:
    async with open_session(name, reg.get(name), secret, timeout=timeout) as s:
        await s.initialize()
        return await s.list_tools()


def describe_server(s: dict) -> str:
    """One line: what a server is and where it lives (never a key)."""
    kind = s.get("kind", "stdio")
    if kind == "http":
        return s["url"]
    if kind == "composio":
        return f"Composio (user {s['user_id']})"
    return shlex.join(s["command"])


def _status_line(entry: dict) -> str:
    if not entry.get("allowed"):
        return "off"
    return "allowed, read-only" if entry.get("readonly") else "allowed"


async def run_cli(args: list[str], config_dir: Path, secret: Callable[[str], str | None], out: Callable[[str], None] = print,
                  ask: Callable[[str], str] = input) -> int:
    reg = McpRegistry(Path(config_dir) / "mcp.json")
    if not args or args[0] in ("-h", "--help", "help"):
        out(USAGE)
        return 0
    cmd, rest = args[0], args[1:]
    try:
        if cmd == "list":
            servers = reg.servers()
            if not servers:
                out("No MCP servers added. See: minty mcp directory")
            for name, s in servers.items():
                out(f"{name}: {describe_server(s)}")
                for tool, entry in sorted(s.get("tools", {}).items()):
                    out(f"  {tool} ({_status_line(entry)})" + ("  [description gives orders: not trusted]" if entry.get("flagged") else ""))
            return 0
        if cmd == "directory":
            for e in DIRECTORY:
                out(f"{e.name}: {e.title} (needs {e.needs})" + (f"\n  Careful: {e.caution}" if e.caution else ""))
            out(DIRECTORY[0].trust + " Add one with: minty mcp add NAME --from-directory ENTRY")
            for name, title, how, caution in REMOTE:
                out(f"{name}: {title} (add with: {how})\n  Careful: {caution}")
            return 0
        if cmd == "add":
            if not rest:
                out(USAGE)
                return 2
            name, opts = rest[0], rest[1:]
            command, env, entry_name, folder = [], [], None, None
            i = 0
            while i < len(opts):
                if opts[i] == "--":
                    command = opts[i + 1:]
                    break
                if opts[i] == "--from-directory" and i + 1 < len(opts):
                    entry_name, i = opts[i + 1], i + 2
                elif opts[i] == "--folder" and i + 1 < len(opts):
                    folder, i = opts[i + 1], i + 2
                elif opts[i] == "--env" and i + 1 < len(opts):
                    env.append(opts[i + 1])
                    i += 2
                else:
                    raise ValueError(f"I do not understand {opts[i]!r}.\n{USAGE}")
            if entry_name:
                entry = find(entry_name)
                if entry is None:
                    raise ValueError(f"{entry_name!r} is not in the directory (minty mcp directory).")
                command = command_for(entry, folder)
            reg.add_server(name, command, env)
            out(f"Added {name}: {shlex.join(command)}")
            if command[0] in ("npx", "uvx", "pipx", "npm", "pip"):
                out("The first run downloads that package from the internet.")
            for key in env:
                out(f"Set its secret with: minty set-key plugin.mcp.{name}.{key}")
            out(f"Next, look at what it offers: minty mcp tools {name}")
            return 0
        if cmd == "add-remote":
            if len(rest) < 2:
                out(USAGE)
                return 2
            name, url, opts, headers, i = rest[0], rest[1], rest[2:], {}, 0
            while i < len(opts):
                if opts[i] == "--header" and i + 1 < len(opts) and "=" in opts[i + 1]:
                    header, _, key = opts[i + 1].partition("=")
                    headers[header] = key
                    i += 2
                else:
                    raise ValueError(f"I do not understand {opts[i]!r}.\n{USAGE}")
            reg.add_remote(name, url, headers)
            out(f"Added {name}: {url}  (host {urlsplit(url).hostname}; only https, only public addresses)")
            out("Anything a tool is given is sent to that server, and what it answers is outside text, not instructions.")
            for key in sorted(set(headers.values())):
                out(f"Set its secret with: minty set-key plugin.mcp.{name}.{key}")
            out(f"Next, look at what it offers: minty mcp tools {name}")
            return 0
        if cmd == "composio":
            if len(rest) != 3 or rest[1] != "--user-id":
                out(USAGE)
                return 2
            reg.add_composio(rest[0], rest[2])
            out(f"Added {rest[0]}: Composio, for the user id {rest[2]!r}.")
            out(f"1. Get your API key in your Composio dashboard, then set it: minty set-key plugin.mcp.{rest[0]}.COMPOSIO_API_KEY")
            out(f"2. Look at what it offers (it makes a session with your key each time): minty mcp tools {rest[0]}")
            out(f"3. Allow only the tools you want: minty mcp allow {rest[0]} TOOL. COMPOSIO_MULTI_EXECUTE_TOOL can act on every account you "
                "connect, so every call of it is asked first, with its arguments shown.")
            out("Composio asks you to connect each of your accounts (Gmail, Slack ...) the first time a tool needs one: it gives a link, "
                "which you open yourself. Those are your connected accounts at Composio; anything a tool is given goes to Composio's servers "
                "and on to the app you connected.")
            return 0
        if cmd == "tools" and len(rest) == 1:
            tools = await _tools(reg, rest[0], secret)
            reg.record_tools(rest[0], tools)
            known = reg.get(rest[0])["tools"]
            for t in tools:
                desc, flagged = sanitize_description(t.get("description", ""))
                st = reg.status(rest[0], t["name"], tool_hash(t))
                hint = " (says it only reads)" if (t.get("annotations") or {}).get("readOnlyHint") else ""
                out(f"{t['name']} [{st}]{hint}: {desc}")
                if flagged:
                    out("  (!) its description gives orders to a model. Do not trust it.")
            return 0
        if cmd == "allow" and len(rest) >= 2:
            name, tool = rest[0], rest[1]
            tools = {t["name"]: t for t in await _tools(reg, name, secret)}
            if tool not in tools:
                raise ValueError(f"{name} has no tool called {tool!r}.")
            t = tools[tool]
            desc, flagged = sanitize_description(t.get("description", ""))
            readonly = "--readonly" in rest
            out(f"{tool}: {desc}")
            if flagged:
                out("(!) this description gives orders to a model; I would not allow it.")
            if readonly and not (t.get("annotations") or {}).get("readOnlyHint"):
                out("Note: the server does not say this tool only reads; calling it read-only is your judgment.")
            if "--yes" in rest or ask(f"Allow {tool} on {name}{' as read-only' if readonly else ''}? [y/N] ").strip().lower().startswith("y"):
                reg.allow(name, tool, tool_hash(t), readonly, t.get("description", ""))
                out(f"Allowed {tool} on {name}" + (" (read-only)." if readonly else ". Each call will ask you first."))
            else:
                out("Not allowed.")
            return 0
        if cmd == "revoke" and len(rest) == 2:
            out(f"Revoked {rest[1]}." if reg.revoke(rest[0], rest[1]) else f"{rest[1]} was not allowed.")
            return 0
        if cmd == "remove" and len(rest) == 1:
            out(f"Removed {rest[0]}." if reg.remove_server(rest[0]) else f"There is no server called {rest[0]!r}.")
            return 0
    except (ValueError, McpError) as e:
        out(str(e))
        return 1
    out(USAGE)
    return 2
