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
from urllib.parse import urlsplit

from minty_core.fsutil import atomic_write_text
from minty_core.provenance import looks_like_injection

NAME = re.compile(r"[a-z][a-z0-9_]{1,23}")
ENV_KEY = re.compile(r"[A-Z][A-Z0-9_]{1,40}")
MAX_ARGS, MAX_ARG_LEN, MAX_ENV = 20, 300, 8
HEADER_NAME = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,39}")
# headers Minty sets itself (or that would change what a request means): a registered header may not be one of these
RESERVED_HEADERS = frozenset({"host", "content-length", "content-type", "accept", "accept-encoding", "connection", "transfer-encoding",
                              "mcp-session-id", "mcp-protocol-version", "cookie", "user-agent", "upgrade", "te", "trailer"})
MAX_HEADERS = 4
USER_ID = re.compile(r"[A-Za-z0-9_.@-]{1,64}")
LOCAL_SUFFIXES = (".local", ".internal", ".lan", ".home", ".localdomain")


def check_remote_url(url: str) -> str:
    """A remote server's address: plain https, a public-looking name (no password in it, no address literal, nothing local)."""
    text = str(url or "").strip()
    parts = urlsplit(text)
    host = (parts.hostname or "").lower().rstrip(".")
    try:
        port = parts.port
    except ValueError:
        port = -1
    if (parts.scheme != "https" or not host or parts.username or parts.password or port not in (None, 443) or len(text) > 300
            or parts.fragment or any(c in text for c in " \t\r\n\0")):
        raise ValueError("A remote server needs a plain https address (no password in it, no port other than 443).")
    if "." not in host or host.replace(".", "").isdigit() or ":" in host or host == "localhost" or host.endswith(LOCAL_SUFFIXES):
        raise ValueError("A remote server needs a public name (not an address number, localhost or a local name).")
    return text


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
            raise ValueError(f"There is no server called {name!r}. Add one at the terminal: minty mcp add.")
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

    def add_remote(self, name: str, url: str, headers: dict | None = None) -> None:
        """A server reached over https. `headers` maps a header name to the NAME of the secret that holds its value (set with
        `minty set-key plugin.mcp.NAME.SECRET`): the value itself is never kept here."""
        if not NAME.fullmatch(str(name)):
            raise ValueError("A server name is lowercase letters, digits and underscores, 2 to 24 characters.")
        url = check_remote_url(url)
        headers = dict(headers or {})
        if len(headers) > MAX_HEADERS or any(not HEADER_NAME.fullmatch(str(h)) or str(h).lower() in RESERVED_HEADERS for h in headers):
            raise ValueError(f"Headers are plain names (at most {MAX_HEADERS}), and not ones Minty sets itself.")
        if any(not isinstance(k, str) or not ENV_KEY.fullmatch(k) for k in headers.values()):
            raise ValueError("Each header takes the name of a secret in UPPER_CASE_WORDS (its value is set with minty set-key).")
        data = self._load()
        if name in data["servers"]:
            raise ValueError(f"{name} is already added.")
        data["servers"][name] = {"kind": "http", "url": url, "headers": headers, "env_keys": sorted(set(headers.values())),
                                 "added": self._clock(), "tools": {}}
        self._save(data)

    def add_composio(self, name: str, user_id: str) -> None:
        """Composio: its tools for the accounts YOU connect there. A fresh session is made each time Minty connects, with your own
        Composio key (`minty set-key plugin.mcp.NAME.COMPOSIO_API_KEY`)."""
        if not NAME.fullmatch(str(name)):
            raise ValueError("A server name is lowercase letters, digits and underscores, 2 to 24 characters.")
        if not USER_ID.fullmatch(str(user_id or "")):
            raise ValueError("The user id is up to 64 letters, digits and . _ @ - (it names you in your Composio project).")
        data = self._load()
        if name in data["servers"]:
            raise ValueError(f"{name} is already added.")
        data["servers"][name] = {"kind": "composio", "user_id": str(user_id), "env_keys": ["COMPOSIO_API_KEY"], "added": self._clock(), "tools": {}}
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
