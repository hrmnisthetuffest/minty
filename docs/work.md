# Handing work over

Minty can take a goal, work on it in the background, tell you what needs you, and prove what it did. This page says what each part
does, how it is kept honest, and what has and has not been tested.

The rule behind all of it: **an outcome is what the evidence shows, not what the worker says.** A step in which no tool was used
cannot pass for work, a coding job whose tests fail is reported as failing, and a message that may not have been sent is said to
be unknown, not sent.

## Missions: "here is the goal"

Say what you want, not the steps: "find my notes about the trip, compare the options and tell me which is best". Minty plans it,
works in the background with the tools your goal needs, and checks each step against a ledger of what the tools really returned.

- It ends **done** (and says whether every step was double-checked), **failed** (with the reason), or **blocked** on one question
  only you can answer. `missions_answer` gives the answer and it carries on; `missions_resume` picks up a failed or interrupted one
  from its saved plan. A restart loses at most the step in progress.
- `missions_report` is the proof: the plan with what was checked, every action with its result, and the final answer.
- `missions_review` reads that record and says what could work better (a tool that keeps failing, goals that run out of steps). It
  gives evidence and proposals; it never changes a setting, a prompt or a permission by itself.
- Steps that need your yes (sending, deleting, anything the safety rules ask about) wait for it through the usual approvals (the
  panel, your voice, Telegram). If you do not answer, that is a no. A mission never gets the terminal, the installer or other
  helpers, and what it reads from web pages or e-mails is treated as untrusted exactly as in a conversation.
- Settings (Plugins, missions): missions at once, seconds and steps per mission.

## Coding agents: "have Claude Code do it"

`agents_start_task` runs Claude Code (or Hermes, OpenClaw) on a project folder, and asks first every time. Afterwards Minty does not
take the agent's word for it:

1. The folder is compared before and after, so "nothing changed" and "these files changed" are facts.
2. The project's own tests are run (found from its files, or the command you set in `check_command`), with no keys in their
   environment. If they fail, the agent is sent back with the failure, up to `max_rounds` times.
3. The verdict is what is reported: **verified** (files changed and tests pass), **not confirmed** (changed, nothing to test),
   **tests failing**, or **nothing changed**. The last two count as failed jobs.

`mode: ask` runs Claude Code read-only for questions; `wait: true` (used inside missions) blocks until the check is done. Claude is
told not to push, reset, rebase or rewrite history. These are guard rails, not a sandbox: an agent that can run `python` can run
anything a script can. While a job runs, what it is doing is read from the disk ("3 files changed so far"). Jobs survive a restart as
"interrupted". `claude_max_budget_usd` caps spending per run.

## E-mail: the outbox, under rules only you write

Off until you switch on the `outbox` plugin and give it an address and an app password (`minty set-key plugin.outbox.password`).
`mail` stays read-only and never sends.

- **Without asking**, only a reply that meets all of this: one recipient who is on your `auto_reply_to` list (addresses or
  `@domains`), answering a message that passed SPF/DKIM/DMARC at your mail server, short, with no links, no other addresses, no
  secrets and none of the sensitive words (passwords, bank, pay, legal ...), under a daily cap. It says Minty sent it.
- **Everything else asks**, with the whole message and the reasons shown, and is asked whatever your safety mode. That includes every
  new conversation. A reply can only go to the person who wrote; the model cannot redirect it.
- Every message is recorded: drafted, sent (and whether automatically or after your yes), failed, or **unknown** if the connection
  dropped (it is never sent twice; check your Sent folder). `follow_up_days` puts "waiting for a reply" on your list.
- No tool can change these rules; they live in settings only you edit. To give Minty more independence, add a person to
  `auto_reply_to` yourself, after you have watched it handle a few.

## What needs you

Every ten minutes Minty looks for a mission waiting for your answer, an interrupted or failed one, an e-mail that may not have gone
out, a draft left waiting a day, an overdue item, a follow-up that has come due. Each is said at most three times, with a pause
between, never more than five a day in all, and through the same gate as everything Minty starts by itself (night hours, the hourly
limit, the off switches; `proactive.attention` turns it off). `attention_review` shows the list; `attention_dismiss` and
`attention_snooze` silence one. A resolved item disappears.

## Brain dumps

`butler_sort` turns a ramble into proposals: commitments ("I need to", "I'll", "I promised"), follow-ups ("waiting on mum"), project
work ("the reel needs a better ending"), ideas ("maybe", "could"), and decisions ("should I post on Reddit first?"). Nothing is saved
until you pick numbers; what is already on your list is skipped, and the original ramble is filed once as a note.

## How it is measured

`PYTHONPATH=src python -m tests.agent_eval.work_eval` runs ten scenarios with failures injected on purpose (a worker that claims work it
did not do, a failing tool, a crash, an injected instruction, a spoofed sender, a coding agent that changed nothing) and counts how
often Minty reported anything but the truth. They run in the normal test suite, with a stored baseline that may not get worse.

## What has and has not been tested

- Tested with scripted models and fake tools: the mission loop, resume, honest outcomes, the gate, the coding verdicts and fix loop,
  the outbox against a local SMTP server, the sender checks against a pretend mailbox, attention, brain dumps, the evaluation.
- Checked against the real thing: the Claude Code flags used (`--disallowedTools`, `--max-budget-usd`, `--resume`) exist in the
  installed CLI.
- **Not yet run:** a mission or a coding job against a real model, e-mail against a real mail server, or the approval round trip over
  Telegram. Expect to watch the first few. How well a real model plans and picks tools is not something these tests can promise.
