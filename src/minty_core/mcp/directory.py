"""A short starting list of MCP servers (692): the reference servers published by the MCP project. This is a list of where to
begin, not a security audit: the code was not audited here and the versions are not pinned. Adding one is a terminal command you
run yourself, and its first run downloads the package."""
from __future__ import annotations

import re
from dataclasses import dataclass

TRUST = "Reference server from the MCP project; its code was not audited here and the version is not pinned."


@dataclass(frozen=True)
class Entry:
    name: str
    title: str
    command: tuple[str, ...]
    needs: str
    caution: str = ""
    needs_folder: bool = False
    trust: str = TRUST


DIRECTORY = (
    Entry("memory", "A small knowledge graph the model can write to and read from", ("npx", "-y", "@modelcontextprotocol/server-memory"),
          "Node (npx)"),
    Entry("time", "The time in any time zone, and conversions", ("uvx", "mcp-server-time"), "uv (uvx)"),
    Entry("fetch", "Fetch a web page as text", ("uvx", "mcp-server-fetch"), "uv (uvx)",
          caution="It fetches addresses from your PC, including ones on your home network, and returns outside text."),
    Entry("filesystem", "Read and change files inside ONE folder", ("npx", "-y", "@modelcontextprotocol/server-filesystem"),
          "Node (npx)", caution="It can read and change everything inside the folder you give it; give it only that folder.",
          needs_folder=True),
    Entry("sequential_thinking", "A scratchpad for step-by-step problem solving",
          ("npx", "-y", "@modelcontextprotocol/server-sequential-thinking"), "Node (npx)"),
)


# Servers reached over https (not a program on this computer): where they fit, how to add one, and what it means.
REMOTE = (
    ("composio", "Hundreds of apps (Gmail, Calendar, Slack, GitHub ...) through YOUR Composio account",
     "minty mcp composio NAME --user-id ID",
     "needs your own Composio account and API key; the accounts you connect there become reachable, and anything a tool is given goes to "
     "Composio and on to that app. Its execute tool can act on all of them, so every call of it is asked first with its arguments shown."),
)


def find(name: str) -> Entry | None:
    key = str(name).strip().lower().replace("-", "_")
    return next((e for e in DIRECTORY if e.name == key), None)


def command_for(entry: Entry, folder: str | None = None) -> list[str]:
    cmd = list(entry.command)
    if entry.needs_folder:
        if not folder:
            raise ValueError(f"{entry.name} needs a folder: --folder /path/to/folder.")
        if not re.fullmatch(r"/[A-Za-z0-9_./ ~+-]{1,200}", folder) or ".." in folder.split("/"):
            raise ValueError("Give the folder as a plain absolute path.")
        cmd.append(folder)
    return cmd
