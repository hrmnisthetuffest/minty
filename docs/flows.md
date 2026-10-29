# Automations (workflows): one system that you and Minty both operate

A workflow is a graph of real operations: a trigger, steps, branches and a result. You draw it in the control panel (**Automations**); Minty reads
and changes the very same definition through its own tools (`flows_*`). There is one stored definition per workflow, so neither side works on a copy.
(This is separate from the n8n exports described in [automations.md](automations.md): those make files for an n8n you run yourself; these run inside Minty.)

## The model
* **One JSON document** per version: `{name, description, nodes:[{id, type, params, pos}], edges:[{from, port, to}]}`. `pos` is where the node sits on the canvas, so
  the picture is part of the definition. Everything (the editor, Minty's tools, the validator, the engine) reads this document.
* **Versions are immutable.** Saving adds one; going back is a new version that copies an old one; "delete" is archive. Nothing is lost, and a run records the version
  it started on, so editing a workflow while it runs changes the next run, never the one going.
* **The same validation on save and on run:** unknown node types or settings, missing required settings, ports that do not exist, connections into a trigger, circles,
  a tool or child workflow that is not there, bad `HH:MM` times, over-long loops, references to nodes that do not exist (an error) or do not come before the node
  (a warning). A workflow with errors cannot be saved or switched on. A workflow that went stale (a tool was removed) is *blocked* when run, with the reason, and no step runs.

## Nodes (every one has a real implementation)
Triggers: **Manual start**, **Schedule** (time of day, days), **A job ended** (kind, status). Data: **Set values**, **Filter list**, **Map list**, **Merge**, **Validate**
(`ok` / `invalid`). Logic: **If** (`true` / `false`). Actions: **Run a tool** (any Minty tool). AI: **Ask the model** (bounded; `choices` or `fields`). Flow: **Run a
workflow** (a child), **For each item** (a child per item, at most 50), **Wait** (at most 30 s). Output: **Tell the user**, **Result**. Every node can have an `error`
path, `retry` (0-3, only for transient failures of steps that change nothing) and `timeout_s`. Expressions are paths only: `{{input.x}}`, `{{nodes.id.field}}`,
`{{prev.field}}`, `{{item.field}}`, with conditions `{path, op, value}` and `all` / `any` / `not`. There is no `eval` and no code node.

## How a run behaves
* A run is a background **job**: a time budget that does not count the time it waits for your answers, cancel, resume after a restart (finished steps are not repeated), and
  its questions go where the request was made (Telegram or the laptop).
* Nodes run in waves; independent branches run together (four at most). A node runs only if a connection into it is *active* (the source finished and left through that
  port); otherwise it is skipped, and so is what only it could feed. One branch failing does not touch the others.
* **Tool steps go through the mission worker**: the same safety gate, receipts, secret checks and provenance as everything else. A graph cannot bypass them, and a workflow
  step cannot call the `flows_*` tools (no recursion). Class 2 and 3 steps are asked, as always, unless your own words already asked for them.
* **Test mode** runs what only reads and asks the model, and *assesses* what would change something (a tool of class 2 or more, a message to you) without doing it. A real run
  does them.
* Statuses are truthful: `done` means it ran; **`verified`** only when a Validate passed or a Result's `require` fields were filled. `failed` and `blocked` (something missing or
  refused) say which step and why; `cancelled` and `interrupted` are not successes. Each step keeps its input, output, timing and error, with secret-looking fields masked.
* The model step puts data in a `<data>` block it is told is untrusted, and its answer is checked against the shape asked for. What a tool or the model returned never
  grants a later step anything: each tool step is gated on its own.

## Minty's tools (`flows_*`)
`catalogue`, `list`, `get` (any workflow, whoever made it), `check`, `save`, `patch` (add/remove nodes, set params, connect, move, rename), `run` (test or real, with input, or
resume), `runs` (a run with steps and a diagnosis, or recent runs and stats), `history` (versions, or a diff), `restore`, `set` (turn on/off, archive). There is no ownership check:
a workflow you drew by hand is read, changed, tested and run exactly like one Minty made. Editing, testing and running are not questions; **turning a workflow on** (it then runs by
itself) is asked unless your words asked for it ("schedule it every morning").

## Triggers
Schedules are checked every minute by the same loop as recipes; the same minute never starts the same workflow twice. When a background job ends, the workflows waiting for it start
(a workflow's own jobs never start workflows, so nothing chains forever). Every event has a key, so the same event never starts the same work twice.

## The panel
Workflow list, a searchable node palette, a canvas (pan, zoom, fit, tidy, minimap, drag to connect), a settings form for the selected node, live problems, runs with per-step status
that light up on the canvas as they happen, versions with "what changed" and restore, and an **Ask Minty** box (it works on the same workflow with its own tools, then the editor reloads it).
Routes: `/api/flows...` (listed in `src/minty_core/flows/api.py`).

## Not built (and why)
* Webhook triggers (no public endpoint exists), and triggers for external apps other than through a Composio tool step.
* Loops inside one graph (use For each over a child workflow, which has its own limits).
* Waiting for an outside event in the middle of a run (a run can wait for your answer, not for the world).
* Evidence-based self-optimisation that runs by itself: Minty can measure (`flows_runs` stats, per-step timing, call counts) and propose changes, but nothing rewrites a workflow
  without being asked.
* A live model run of any of this has not been done in this session (the tests drive the real tools with scripted data).
