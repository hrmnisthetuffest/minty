# Extra tools through MCP

Minty can call tools on [MCP](https://modelcontextprotocol.io) servers you add yourself. Adding a server, listing its tools and
allowing each tool is done at the terminal only (`minty mcp ...`); a tool you have not allowed never runs, each call asks
first, and a tool whose description changes later is switched off until you look at it again. Turn MCP on with `mcp.enabled`
in the settings.

Minty's MCP client talks to a server **as a local command** (standard input and output). Servers that live at a web address and
want a key in a header are not supported yet.

> The notes below come from each project's own page on 2026-10-27. **None of them has been run with Minty.** Read the project's
> own instructions and terms before you install anything.

## api-anything: turn a website you use into callable operations

[api-anything](https://github.com/goodnight000/api-anything) (MIT, Node 22.13 or newer) learns how a website's own pages make
their requests, then calls the site over plain HTTP. It can run as an MCP server, so it fits Minty's client:

```bash
npx -y api-anything setup
minty mcp add api-anything -- api-anything mcp
minty mcp tools api-anything
minty mcp allow api-anything TOOL --readonly
```

- Its page says to leave writes off unless you need them (`--allow-writes` is a separate switch), and to start with a read-only
  tool. Minty's `--readonly` marks a tool the same way.
- It reuses the sign-in your browser already has, through its own `login` command. That is access to your account: only use it
  for accounts that are yours, and keep the session file private.
- Its page says: "Read each site's terms. You're responsible for how you use it." Some sites forbid automated access, and
  school or workplace systems often do. If the terms are not clear, do not automate it.
- It can optionally share what it has learned to a shared catalogue. Leave that off unless you have read what is shared.

## What is not here, and why

| Project | Status |
|---|---|
| **Composio** (`https://connect.composio.dev/mcp`) | A hosted server that needs your own Composio account and an API key sent as a header. Minty's client does not speak that transport yet, so it cannot be connected today. Supporting it means a new network transport that sends a key to a remote host; that is a separate piece of work to design and test with a real account. |
| **Agent-Reach** | A Python command-line tool that reads sites such as Twitter, Reddit and YouTube, and copies login sessions from your browser for some of them. It is not an MCP server, so nothing in Minty calls it. Anything it reads would be untrusted text from strangers. |
| **billion-context** | A local proxy that sits in front of every model call from a client such as Claude Code and rewrites the traffic. Minty does not send its model calls through Claude Code's API traffic, and its licence adds an attribution requirement for products that use it. Not a fit. |
