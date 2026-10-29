# What would make Minty frustrating as a replacement for JARVIS (assessed against the code)

Each item says what was checked. **Confirmed** = seen in the code or a test. **To investigate** = suspected, not yet shown. The first column of fixes is what this round changed.

| # | Limitation | Status | Evidence | What changed |
|---|---|---|---|---|
| 1 | No tool for the user's own automations: nothing to see, edit or debug a multi-step process as a graph; recipes are saved missions, n8n only exports files | Confirmed | `recipes.py`, `integrations/n8n_templates.py` (export only), no graph model anywhere | `minty_core/flows/` (model, engine, store, service), `flows_*` tools, Automations page |
| 2 | Missions run their steps one at a time | Confirmed | `agentloop.py` executes one checkpoint at a time (no `gather`); `docs/autonomy.md` | Workflows run independent branches in parallel; missions unchanged |
| 3 | A running mission's goal cannot be changed: the tools are start / answer / resume / review / report | Confirmed | `plugins/builtin/missions/plugin.py` tools | **Not fixed.** Workflows are edited freely; a mission still has to be restarted with the new goal |
| 4 | Replanning exists only after a failed check inside a mission (`_replan`, `MAX_REPLANS`), not when the situation changes | Confirmed | `agentloop.py` `_replan` is called from the failure ladder | Not changed |
| 5 | A job cannot wait for an outside event, only for the user's answer | Confirmed | `jobs.py` has `waiting_approval` and `needs_user`, nothing else | Workflows *start* on events (a job ended, a schedule); waiting in the middle is not built |
| 6 | A time limit counted the time a job waited for the user, so a mission was "stopped for taking too long" while parked on a question | Confirmed (mission 14) | `jobs.py` used a wall-clock `wait_for` | Fixed earlier this session (`_within_budget`, `Budget.waited`); the workflow engine uses the same rule for step timeouts |
| 7 | Questions and reports went to every channel, wherever the request was made | Confirmed | `telegram/approvals.py` offered every job approval | Fixed earlier this session (`origin.py`) |
| 8 | Minty did not know which apps are connected through Composio, so it said it had no access | Confirmed | the hint listed a few tool names | Fixed earlier this session (`composio_apps.py`) |
| 9 | "Done" is not always "verified": most tools report success when they ran | Partly confirmed | missions have a verifier; a single tool call has none | Workflow runs carry `verified` separately from `done` (a Validate passed, or a Result's `require` fields filled) |
| 10 | A model's judgment of how big or ambiguous a request is is a keyword rule (`workflow.brief`) | To investigate | `workflow.py` is word rules by design | Not changed |

## Built in this round (all in `dev/`, nothing pushed)
Workflow model with one canonical JSON definition and one validator; safe expression language; 16 real node types; an engine with parallel branches, error paths, retry for transient
failures only, timeouts that leave out waiting, test mode that assesses side effects, resume without repeating finished steps, cancellation, honest `done / verified / failed / blocked`;
versioned store (restore, archive, no deletion); runs as jobs (budget, cancel, resume, origin); schedules and job-ended triggers with de-duplication; 11 agent tools with no ownership
check; a panel page (canvas, palette, properties, live run status, versions, assistant); acceptance tests for the ten scenarios in the request plus boundaries.
See `docs/flows.md`.

## Verified, and not
* Verified: the engine rules (mutation-checked), the agent tools against the real job runner and stores, the HTTP routes, and the page in a real browser against the real core app
  (render, add a node with live problems, test run with nodes lighting up through the events socket, real run with stats, per-step table).
* Live model (scratch copy, real provider): built a workflow from one sentence in 3 tool calls with no questions and reported the test honestly (not verified, not switched on); edited it into
  a valid second version. The first attempts ran out of tool rounds guessing the condition syntax, which led to the catalogue example and to accepting `input.n > 10` and `{left, op, right}`
  (stored canonically). One edit run's final summary hit a provider timeout. Not tried: long, messy requests; debugging a real failure end to end by a live model.
* Not verified: drag-to-connect and node dragging by mouse were written but only
  exercised through the DOM (the connection and move code paths have no automated browser test); a schedule firing across a real day; a Composio tool step in a real run.
