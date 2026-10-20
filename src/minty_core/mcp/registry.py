"""The MCP servers you added and the tools you allowed (302). Everything a server says about itself is outside text: descriptions
are cleaned, cut and scanned for orders; each allowed tool is pinned by a hash of its name, description and schema, and if the
server later changes any of them the tool is off again until you allow it anew. Only the terminal changes this file."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Callable

from minty_core.fsutil import atomic_write_text
from minty_core.provenance import looks_like_injection

NAME = re.compile(r"[a-z][a-z0-9_]{1,23}")
ENV_KEY = re.compile(r"[A-Z][A-Z0-9_]{1,40}")
MAX_ARGS, MAX_ARG_LEN, MAX_ENV = 20, 300, 8


def tool_hash(tool: dict) -> str:
    body = json.dumps({"name": tool.get("name"), "description": tool.get("description", ""),
                       "inputSchema": tool.get("inputSchema", {})}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def sanitize_description(text: str) -> tuple[str, bool]:
    clean = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(text or "")).split())[:300]
    return clean, looks_like_injection(text)


def build_env(name: str, server: dict, secret: Callable[[str], str | None]) -> dict:
    """The only environment a server ever sees: a plain path/home/language plus the secrets you declared for it."""
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp"),
           "LANG": os.environ.get("LANG", "C.UTF-8")}
    for key in server.get("env_keys", []):
        value = secret(f"plugin.mcp.{name}.{key}")
        if value:
            env[key] = value
    return env


class McpRegistry:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path, self._clock = Path(path), clock

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("servers", {})
        return data

    def _save(self, data: dict) -> None:
        atomic_write_text(self.path, json.dumps(data, indent=1, sort_keys=True), mode=0o600)

    def servers(self) -> dict:
        return self._load()["servers"]

    def get(self, name: str) -> dict:
        server = self.servers().get(name)
        if server is None:
            raise ValueError(f"There is no server called {name!r}. Add one at the terminal: minty-core mcp add.")
        return server

    def add_server(self, name: str, command: list[str], env_keys: list[str] | tuple = ()) -> None:
        if not NAME.fullmatch(str(name)):
            raise ValueError("A server name is lowercase letters, digits and underscores, 2 to 24 characters.")
        if (not command or len(command) > MAX_ARGS or any(not isinstance(a, str) or "\0" in a or len(a) > MAX_ARG_LEN for a in command)
                or not command[0] or re.search(r"\s", command[0])):
            raise ValueError("The command is a program followed by its arguments (up to 20), with no spaces in the program name.")
        if len(env_keys) > MAX_ENV or any(not ENV_KEY.fullmatch(k) for k in env_keys):
            raise ValueError("Secret names are UPPER_CASE_WORDS (at most 8).")
        data = self._load()
        if name in data["servers"]:
            raise ValueError(f"{name} is already added.")
        data["servers"][name] = {"command": list(command), "env_keys": list(env_keys), "added": self._clock(), "tools": {}}
        self._save(data)

    def remove_server(self, name: str) -> bool:
        data = self._load()
        gone = data["servers"].pop(name, None) is not None
        if gone:
            self._save(data)
        return gone

    def record_tools(self, name: str, tools: list[dict]) -> None:
        data = self._load()
        entries = data["servers"][name]["tools"]
        for t in tools:
            desc, flagged = sanitize_description(t.get("description", ""))
            old = entries.get(t["name"], {})
            entries[t["name"]] = {"hash": old.get("hash", ""), "allowed": old.get("allowed", False),
                                  "readonly": old.get("readonly", False), "description": desc, "flagged": flagged,
                                  "seen": tool_hash(t)}
        self._save(data)

    def allow(self, name: str, tool: str, current_hash: str, readonly: bool = False, description: str = "") -> None:
        data = self._load()
        entries = data["servers"][name]["tools"]
        desc, flagged = sanitize_description(description or entries.get(tool, {}).get("description", ""))
        entries[tool] = {"hash": current_hash, "allowed": True, "readonly": bool(readonly), "description": desc,
                         "flagged": flagged, "seen": current_hash}
        self._save(data)

    def revoke(self, name: str, tool: str) -> bool:
        data = self._load()
        entry = data["servers"].get(name, {}).get("tools", {}).get(tool)
        if not entry or not entry.get("allowed"):
            return False
        entry["allowed"] = False
        self._save(data)
        return True

    def status(self, name: str, tool: str, current_hash: str) -> str:
        entry = self.servers().get(name, {}).get("tools", {}).get(tool)
        if not entry or not entry.get("allowed"):
            return "off"
        return "allowed" if entry.get("hash") == current_hash else "changed"

    def is_readonly(self, name: str, tool: str) -> bool:
        entry = self.servers().get(name, {}).get("tools", {}).get(tool) or {}
        return bool(entry.get("allowed") and entry.get("readonly"))
