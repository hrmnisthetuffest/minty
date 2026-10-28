# Doing what you asked, without being babysat

What Minty does by itself, how it is kept safe, and what is not done. Each part below has its own page or tests; this is the map.

| You want | What exists | Where |
| --- | --- | --- |
| "Don't ask me to confirm what I just told you to do" | A tool can declare a **mandate**; the safety layer lifts its question when your own words ask for that kind of action, name the people or places involved, and nothing the call uses came from outside content. Deep research asks unless you asked for depth. Deny rules, lockdown and class 3 (money, keys, deletion, people) are untouched. | [privacy-and-safety.md](privacy-and-safety.md), `policy.Mandate` |
| Sheets and docs as part of the work | Composio steps are judged one by one: reads and drafts are free, writes into a sheet or doc **Minty made or you named** are class 2 (and your request answers it), everything else is class 3. Minty finds its sheets again (`artifacts_list`) instead of making a second one. | [mcp-extras.md](mcp-extras.md), `composio_rules.py`, `artifacts.py` |
| It understands what would make the result useful | `workflow.brief`: a plain question gets a direct answer; a goal that researches, compares, ranks or tracks several things gets a short brief for the mission worker (a sheet, a doc, reminders for dates, follow-ups, "read back what you wrote"). If no spreadsheet tool is connected it says so instead of pretending. | `workflow.py` |
| Missions that survive and report | Already there: planner / executor / verifier, a ledger of what tools really returned, resume after a restart, states queued / running / needs you / waiting for approval / done / failed / cancelled / interrupted / blocked. See [work.md](work.md). | `missions.py`, `agentloop.py`, `jobs.py` |
| Notice what matters in Gmail | A watcher that reads only what changed, scores each message from named signals, and tells the notifier. Off until you turn it on. | [proactive-mail.md](proactive-mail.md) |
| One way to interrupt me | `notifier.py`: severity, no repeats, quiet hours, caps, private detail kept off the phone, retries, reminders for an urgent notice nobody dealt with. No phone-call channel exists and none is faked. | [proactive-mail.md](proactive-mail.md) |
| It learns how I work, correctably | `prefs.py`: told / accepted habit / guess / one task / suggestion; scope, confidence, where it came from; a habit seen three times is **offered, never applied** until you accept; corrections lower it and a second removes it; a short fixed list of kinds, so a preference can never touch a permission. | `prefs.py`, the `prefs` plugin |
| External text cannot take over | Unchanged and relied on: provenance tagging, untrusted result wrappers, the mandate ignores anything but your own words, the Gmail watcher shows a message as plain text and never follows it, its Composio steps must each be a read. | `provenance.py`, `policy.py` |

## What is not built, and why

- **Parallel steps inside one mission.** A mission's plan runs one checkpoint at a time against one ledger and one conversation. Parallel
  branches would need separate ledgers and a way to ask you one question at a time; independent jobs (two missions) do run side by side.
- **Automatic replies under your `mailpolicy` rules through Composio.** Sends stay class 3 (always asked) except the one case you can turn
  on (`safety.mandate_send_mail`: one short plain mail to an address you wrote in your own words, five a day). `mailpolicy` (the narrow rules
  you write for automatic replies) governs the local outbox, and Composio's send tools do not go through it; routing replies through it is the
  next step, deliberately not done blind.
- **A "waiting on an outside event" job state** (for example "until Alex replies"). Follow-ups owed are tracked by the attention list; a job that
  waits for an outside event is not a state of its own.
- **Phone calls.** No calling service is integrated. Telegram is the strongest channel.
- **A model's judgment on borderline mail.** `mailscore` flags a result as borderline; nothing yet asks a model about it.
- **A point 11 in your prompt** ("optimize the full workflow") was cut off where it was pasted; nothing was built for it.

## What was not verified

No live-model run of the new behaviours (a mandate against a real voice request; the brief's effect on a real mission); no unattended day of
the Gmail watcher on a real mailbox; no live Composio *create* (the link pattern in Composio's answer is untested); delivery to a phone from
the notifier. The Composio read shapes (profile, history, message headers, account list) were taken from live read-only answers.
