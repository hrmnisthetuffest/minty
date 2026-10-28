# Extra tools through MCP

Minty can call tools on [MCP](https://modelcontextprotocol.io) servers you add yourself. Adding a server, listing its tools and
allowing each tool is done at the terminal only (`minty mcp ...`); a tool you have not allowed never runs, each call asks
first, and a tool whose description changes later is switched off until you look at it again. Turn MCP on with `mcp.enabled`
in the settings.

Minty's MCP client talks to a server in one of three ways: **as a local command** (standard input and output), **over https** (a server
at a web address that wants a key in a header; MCP's "streamable HTTP"), or as **Composio** (below). A remote server is reached only over
https and only at public addresses, through the same guarded fetcher as every other outside request (the name is resolved once and that
address is the one connected to, redirects to another host are refused, the answer's size and the time are capped). Its key lives in
the keystore, never in `mcp.json` or a log line, and is sent only to that address.

```bash
minty mcp add-remote NAME https://host/path --header x-api-key=NAME_API_KEY   # then: minty set-key plugin.mcp.NAME.NAME_API_KEY
minty mcp tools NAME          # look at what it offers (nothing is allowed yet)
minty mcp allow NAME TOOL     # allow one tool, after reading its description
```

## Composio

[Composio](https://composio.dev) gives one MCP connection to many apps (Gmail, Calendar, Slack, GitHub ...) that **you** connect in your own
Composio account. You need that account and its API key; Minty does not create either.

```bash
minty mcp composio composio --user-id YOUR_ID       # YOUR_ID names you in your Composio project
minty set-key plugin.mcp.composio.COMPOSIO_API_KEY  # the key from your Composio dashboard
minty mcp tools composio                            # makes a session with your key and lists what it offers
minty mcp allow composio COMPOSIO_SEARCH_TOOLS --readonly
```

Composio's current API makes a "session" per user (`POST https://backend.composio.dev/api/v3.1/tool_router/session`, key in the
`x-api-key` header) that answers with an MCP address; Minty makes one each time it connects, and only follows an address that is on
`composio.dev` over https. The tools it offers are meta tools (`COMPOSIO_SEARCH_TOOLS` finds an app's actions, `COMPOSIO_MULTI_EXECUTE_TOOL`
runs them, `COMPOSIO_MANAGE_CONNECTIONS` links an account, `COMPOSIO_REMOTE_WORKBENCH` / `..._BASH_TOOL` run code in Composio's sandbox).
When a tool needs an account you have not connected, Composio gives a link: you open it yourself.

### If your dashboard only shows a general MCP (no API key for apps)

Composio's dashboard also hands out a **general MCP address with a consumer key** (`ck_...`) for clients such as Claude Code and Codex:
the address is `https://connect.composio.dev/mcp` and the key goes in an `x-consumer-api-key` header. Minty can use that directly:

```bash
minty mcp add-remote composio https://connect.composio.dev/mcp --header x-consumer-api-key=COMPOSIO_CONSUMER_KEY
minty set-key plugin.mcp.composio.COMPOSIO_CONSUMER_KEY   # paste the ck_ key from the dashboard's Connect > clients page (--show shows it as you paste)
minty mcp tools composio
```

Your apps are tied to that key's account, so there is no user id. Minty treats a server on `composio.dev` added this way exactly like one
added with `minty mcp composio`: the same tools, the same class-3 asking for the ones that act. Composio's OAuth sign-in for clients is not
supported; the key is the way in. Checked against the real address with an invalid key (401); not with a real key.

What that means for you:
- Anything a tool is given goes to Composio's servers and on to the app you connected. Do not give it what you would not send there.
- `COMPOSIO_MULTI_EXECUTE_TOOL` can act on **every** account you connected (send mail, post, delete). It, the workbench, the bash tool and
  the connection and skill managers are class 3: always asked, with the actual arguments shown (recipient, subject ...), however you set the asking mode.
  Mail sent this way does not go through Minty's outbox rules, so allow it only if that is what you want.
- **Sheets and docs.** A Composio call is judged by its steps (`composio_rules.py`, plain checks on the step's name and target):
  reads and Gmail drafts are class 1 (not asked), a write into a Google sheet or doc that **Minty made itself or you named** is class 2 (asked
  only if your own request does not already say it: "organize the findings in my leads sheet" does), and anything else, a send, a delete, a
  calendar invite, a step it does not recognise, or a sheet nobody registered, is class 3 and always asked. Minty learns the sheets and docs it
  creates from Composio's answer (their link), and you can add one yourself by giving its link in your own words ("use this sheet for the
  leads: <link>"); `artifacts_list` shows them, `artifacts_forget` removes one. A page or an e-mail cannot add one. Not checked against a live
  create: the link in Composio's answer is matched by its address, and if Composio changes that, a new sheet is not remembered and its writes are asked.
- The one exception is reading. A `COMPOSIO_MULTI_EXECUTE_TOOL` call whose every step is a read (a slug like `GMAIL_FETCH_EMAILS`,
  `GITHUB_GET_...`, `..._LIST_...`, `..._SEARCH_...`, with no write word such as SEND, CREATE, DELETE, UPDATE in it) is not asked about. One
  write step, or a step Minty cannot tell, and the whole call is asked. The search, schema and wait tools you allowed as read-only are not asked either.
- A server you set up at the terminal counts as a destination you named, so Minty does not ask "this sends data to a new party" about it on
  every call.
- Tools are pinned by a hash of their name, description and inputs, and a changed description switches the tool off until you look and allow
  it again. Composio rewords its own tools from time to time, so for Composio only, a new description is accepted when the tool's **name and
  inputs** are exactly what you allowed (pinned when you allowed it) and the new words give no orders to a model; a tool that asks for
  anything new, or any other server's change, still switches it off. (Found live: Composio had reworded `COMPOSIO_SEARCH_TOOLS` since it was
  allowed, which would have stopped it working.)
- What a tool answers is outside text, shown tagged as such, never followed as instructions.
- Checked against Composio's real endpoint with an invalid key (it answers 401 and Minty reports "did not accept the key"). Everything past
  that needs a real key: **it has not been run with a real Composio account.**

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
| **Agent-Reach** | A Python command-line tool that reads sites such as Twitter, Reddit and YouTube, and copies login sessions from your browser for some of them. It is not an MCP server, so nothing in Minty calls it. Anything it reads would be untrusted text from strangers. |
| **billion-context** | A local proxy that sits in front of every model call from a client such as Claude Code and rewrites the traffic. Minty does not send its model calls through Claude Code's API traffic, and its licence adds an attribution requirement for products that use it. Not a fit. |
