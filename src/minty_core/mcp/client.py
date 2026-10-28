"""A small MCP client over stdio (201): newline-delimited JSON-RPC. It speaks only what it needs (initialize, tools/list,
tools/call), answers every request the SERVER makes with "not supported" (a server can never borrow our model or read our
folders), ignores lines that are not JSON, caps what it reads and keeps, times out, and always ends the server process.
The server gets only the environment it is given."""
from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urlsplit

from minty_core.safefetch import FetchFailed, safe_fetch
from minty_core.tasks import cancel_and_wait

PROTOCOL = "2025-06-18"
MAX_TEXT = 6000


class McpError(Exception):
    pass


@dataclass
class ToolResult:
    text: str
    is_error: bool = False


class _Calls:
    """The three calls Minty uses, over whichever wire the session has (it provides request and notify)."""

    # ---- the three calls we use ------------------------------------------------------------------------
    async def initialize(self) -> dict:
        info = await self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                                 "clientInfo": {"name": "minty", "version": "1"}})
        await self.notify("notifications/initialized")
        return info

    async def list_tools(self) -> list[dict]:
        tools, cursor = [], None
        for _ in range(10):
            res = await self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools += [t for t in res.get("tools", []) if isinstance(t, dict) and isinstance(t.get("name"), str)]
            cursor = res.get("nextCursor")
            if not cursor:
                break
        return tools[:200]

    async def call_tool(self, name: str, arguments: dict, max_chars: int = MAX_TEXT) -> ToolResult:
        res = await self.request("tools/call", {"name": name, "arguments": arguments})
        parts = []
        for c in res.get("content", []):
            kind = c.get("type") if isinstance(c, dict) else None
            parts.append(str(c.get("text", "")) if kind == "text" else f"[{kind or 'content'} omitted]")
        text = "\n".join(parts)
        if len(text) > max_chars:  # the model gets MAX_TEXT; code that parses the answer asks for more
            text = text[:max_chars - 6] + "\n[cut]"
        return ToolResult(text, bool(res.get("isError")))


class McpSession(_Calls):
    def __init__(self, argv: list[str], env: dict, cwd: str | None = None, spawn=asyncio.create_subprocess_exec,
                 timeout: float = 30.0, max_line: int = 1_000_000):
        self.argv, self.env, self.cwd, self._spawn, self.timeout, self.max_line = argv, dict(env), cwd, spawn, timeout, max_line
        self._proc = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next = 0
        self._reader: asyncio.Task | None = None
        self._failure: str | None = None

    async def __aenter__(self) -> "McpSession":
        try:
            self._proc = await self._spawn(*self.argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                           stderr=asyncio.subprocess.DEVNULL, env=self.env, cwd=self.cwd, limit=self.max_line)
        except (OSError, ValueError) as e:
            raise McpError(f"The server could not start ({type(e).__name__}).") from None
        self._reader = asyncio.get_running_loop().create_task(self._read_loop())
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    def exited(self) -> bool:
        return self._proc is None or self._proc.returncode is not None

    async def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.returncode is None:
            try:
                proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                await asyncio.wait_for(proc.wait(), 1.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        await cancel_and_wait(self._reader)
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(McpError("The server was closed."))

    # ---- wire ------------------------------------------------------------------------------------------
    async def _send(self, obj: dict) -> None:
        try:
            self._proc.stdin.write((json.dumps(obj) + "\n").encode())
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, AttributeError):
            raise McpError("The server is not running.") from None

    async def _read_loop(self) -> None:
        try:
            while True:
                try:
                    raw = await self._proc.stdout.readline()
                except (ValueError, asyncio.LimitOverrunError):
                    self._failure = "The server's answer was too large, so I stopped it."
                    self._proc.kill()
                    break
                if not raw:
                    break
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                mid = msg.get("id")
                if mid is not None and ("result" in msg or "error" in msg) and "method" not in msg:
                    fut = self._pending.pop(mid, None) if isinstance(mid, int) else None
                    if fut is not None and not fut.done():
                        fut.set_result(msg)
                elif "method" in msg and mid is not None:
                    await self._decline(msg)
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(McpError(self._failure or "The server stopped."))

    async def _decline(self, msg: dict) -> None:
        if msg.get("method") == "ping":
            await self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
        else:
            await self._send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "not supported"}})

    async def request(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        self._next += 1
        mid = self._next
        fut = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        await self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})
        try:
            msg = await asyncio.wait_for(fut, timeout or self.timeout)
        except asyncio.TimeoutError:
            self._pending.pop(mid, None)
            raise McpError("The server did not answer in time.") from None
        if "error" in msg:
            raise McpError("The server refused: " + " ".join(str((msg["error"] or {}).get("message", "error")).split())[:120])
        return msg.get("result") or {}

    async def notify(self, method: str, params: dict | None = None) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})


# ---- a server reached over https (streamable HTTP) ------------------------------------------------------------------------
async def _post(method: str, url: str, *, headers: dict, body: dict | None, timeout: float, max_bytes: int):
    """One request through the same guarded fetcher as every other outside request: public addresses only (the name is resolved once and
    that address is the one connected to), redirects to another host refused, the answer's size and the time capped. The headers go only
    to this address."""
    try:
        return await safe_fetch(method, url, json_body=body, auth_headers=headers, keep_host=(urlsplit(url).hostname or "").lower(),
                                max_bytes=max_bytes, deadline=timeout)
    except ValueError:
        raise McpError("That address is not a public one, so I did not connect.") from None
    except FetchFailed as e:
        why = str(e)
        raise McpError("The server did not answer in time." if "timeout" in why else
                       "The server tried to send me to another host, so I stopped." if "redirect" in why else
                       "The server could not be reached.") from None


class McpHttpSession(_Calls):
    """The same three calls as a local server, over https. Every request is a POST of one JSON-RPC message; the answer is JSON or a short
    event stream. A request the SERVER makes is answered "not supported" (a server can never borrow our model). The session id the server
    gives is sent back; the session is ended politely at the close. Header values (keys) are used for the request and never repeated in a
    message."""

    def __init__(self, url: str, headers: dict, *, post=None, timeout: float = 30.0, max_bytes: int = 1_000_000):
        self.url, self.headers, self.timeout, self.max_bytes = url, dict(headers), timeout, max_bytes
        self._post = post or _post
        self._next = 0
        self._session_id: str | None = None

    async def __aenter__(self) -> "McpHttpSession":
        parts = urlsplit(self.url or "")
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise McpError("A remote server needs a plain https address (no password in it).")
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        sid, self._session_id = self._session_id, None
        if sid:
            try:
                await self._send("DELETE", None, session=sid)
            except McpError:
                pass  # ending it politely is a courtesy

    def exited(self) -> bool:
        return False

    async def _send(self, method: str, body: dict | None, session: str | None = None):
        headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": PROTOCOL, **self.headers}
        sid = session or self._session_id
        if sid:
            headers["Mcp-Session-Id"] = sid
        try:
            got = await asyncio.wait_for(self._post(method, self.url, headers=headers, body=body, timeout=self.timeout, max_bytes=self.max_bytes),
                                         self.timeout + 1)
        except asyncio.TimeoutError:
            raise McpError("The server did not answer in time.") from None
        if got.truncated:
            raise McpError("The server's answer was too large, so I stopped reading it.")
        if got.status in (401, 403):
            raise McpError(f"The server did not accept the key (HTTP {got.status}). Check it with: minty set-key.")
        if got.status == 404:
            raise McpError("The server ended the session." if sid else "No server answers at that address (HTTP 404).")
        if got.status == 429:
            raise McpError("The server says there have been too many requests (HTTP 429); try again later.")
        if got.status >= 400:
            raise McpError(f"The server answered with an error (HTTP {got.status}).")
        return got

    @staticmethod
    def _messages(got) -> list:
        kind = got.content_type.split(";")[0].strip().lower()
        text = got.data.decode("utf-8", "replace")
        if kind == "application/json":
            try:
                data = json.loads(text)
            except ValueError:
                raise McpError("That was not an MCP answer.") from None
            return data if isinstance(data, list) else [data]
        if kind == "text/event-stream":
            out = []
            for block in re.split(r"\r?\n\r?\n", text):
                data = "\n".join(line[5:].lstrip() for line in block.splitlines() if line.startswith("data:"))
                if data:
                    try:
                        out.append(json.loads(data))
                    except ValueError:
                        continue
            return out
        raise McpError("That was not an MCP answer.")

    async def _decline(self, msg: dict) -> None:
        reply = {"jsonrpc": "2.0", "id": msg["id"], "result": {}} if msg.get("method") == "ping" else \
            {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "not supported"}}
        try:
            await self._send("POST", reply)
        except McpError:
            pass

    async def request(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        self._next += 1
        mid = self._next
        got = await self._send("POST", {"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})
        if method == "initialize":
            sid = got.headers.get("mcp-session-id", "")
            if sid and re.fullmatch(r"[\x21-\x7e]{1,128}", sid):
                self._session_id = sid
        answer = None
        for msg in self._messages(got):
            if not isinstance(msg, dict):
                continue
            if msg.get("id") == mid and ("result" in msg or "error" in msg) and "method" not in msg:
                answer = msg
            elif "method" in msg and msg.get("id") is not None:
                await self._decline(msg)
        if answer is None:
            raise McpError("The server did not answer that request.")
        if "error" in answer:
            raise McpError("The server refused: " + " ".join(str((answer["error"] or {}).get("message", "error")).split())[:120])
        return answer.get("result") or {}

    async def notify(self, method: str, params: dict | None = None) -> None:
        await self._send("POST", {"jsonrpc": "2.0", "method": method, "params": params or {}})


COMPOSIO_SESSION = "https://backend.composio.dev/api/v3.1/tool_router/session"


def composio_host_ok(url: str) -> bool:
    parts = urlsplit(str(url or ""))
    host = (parts.hostname or "").lower().rstrip(".")
    return (parts.scheme == "https" and not parts.username and not parts.password and parts.port in (None, 443)
            and (host == "composio.dev" or host.endswith(".composio.dev")))


async def _composio_address(user_id: str, key: str, timeout: float, post) -> str:
    """A fresh Composio session for this user, made with their own key: where to connect. The address it names must be Composio's own."""
    post = post or _post
    try:
        got = await post("POST", COMPOSIO_SESSION, headers={"x-api-key": key, "Accept": "application/json"}, body={"user_id": user_id},
                         timeout=timeout, max_bytes=200_000)
    except McpError as e:
        raise McpError(f"Composio: {e}") from None
    if got.status in (401, 403):
        raise McpError("Composio did not accept the key. Check it: minty set-key plugin.mcp.NAME.COMPOSIO_API_KEY.")
    if got.status >= 400:
        raise McpError(f"Composio answered with an error (HTTP {got.status}).")
    try:
        url = (json.loads(got.data.decode("utf-8", "replace")).get("mcp") or {}).get("url")
    except (ValueError, AttributeError):
        raise McpError("Composio sent an answer I could not read.") from None
    if not isinstance(url, str) or not url:
        raise McpError("Composio's answer had no MCP address.")
    if not composio_host_ok(url):
        raise McpError("Composio sent an address that is not one of its own, so I did not connect.")
    return url


@asynccontextmanager
async def open_session(name: str, server: dict, secret, timeout: float = 30.0, post=None):
    """A session for any kind of server you added: a local program, a server over https, or Composio. Secrets come from the keystore
    (plugin.mcp.NAME.KEY); a missing one says how to set it and nothing is contacted."""
    from minty_core.mcp.registry import build_env

    kind = server.get("kind", "stdio")

    def need(key: str) -> str:
        value = secret(f"plugin.mcp.{name}.{key}")
        if not value:
            raise McpError(f"{name} needs its key ({key}). Set it with: minty set-key plugin.mcp.{name}.{key}")
        return value

    if kind == "stdio":
        session = McpSession(server["command"], build_env(name, server, secret), timeout=timeout)
    elif kind == "http":
        session = McpHttpSession(server["url"], {header: need(key) for header, key in (server.get("headers") or {}).items()}, post=post, timeout=timeout)
    elif kind == "composio":
        key = need("COMPOSIO_API_KEY")
        session = McpHttpSession(await _composio_address(server["user_id"], key, timeout, post), {"x-api-key": key}, post=post, timeout=timeout)
    else:
        raise McpError(f"{name} is a kind of server I do not know ({kind}).")
    async with session as live:
        yield live
