"""A small MCP client over stdio (201): newline-delimited JSON-RPC. It speaks only what it needs (initialize, tools/list,
tools/call), answers every request the SERVER makes with "not supported" (a server can never borrow our model or read our
folders), ignores lines that are not JSON, caps what it reads and keeps, times out, and always ends the server process.
The server gets only the environment it is given."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

PROTOCOL = "2025-06-18"
MAX_TEXT = 6000


class McpError(Exception):
    pass


@dataclass
class ToolResult:
    text: str
    is_error: bool = False


class McpSession:
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
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
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

    async def call_tool(self, name: str, arguments: dict) -> ToolResult:
        res = await self.request("tools/call", {"name": name, "arguments": arguments})
        parts = []
        for c in res.get("content", []):
            kind = c.get("type") if isinstance(c, dict) else None
            parts.append(str(c.get("text", "")) if kind == "text" else f"[{kind or 'content'} omitted]")
        text = "\n".join(parts)
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT - 6] + "\n[cut]"
        return ToolResult(text, bool(res.get("isError")))
