# Gmail that tells you what matters, and one place for notices

Minty can watch your Gmail through the Composio server you already set up (see [mcp-extras.md](mcp-extras.md)) and tell you about the
messages that matter before you remember to look. It is **off until you turn it on**, because it reads your mail.

## Turn it on

1. Composio must be set up and these allowed (`minty mcp tools composio` to look first):
   ```bash
   minty mcp allow composio COMPOSIO_MULTI_EXECUTE_TOOL
   minty mcp allow composio COMPOSIO_SEARCH_TOOLS --readonly
   ```
   (The second is only how it finds your Gmail accounts; you can instead list them in the setting `gmail_watch.accounts`.) If you have not
   allowed the first one, or its description changed since you did, the watcher says so once, with the command, and reads nothing.
2. Switch on **Watch your Gmail** on the panel's Switches page (`gmail_watch.enabled`). Other settings: `gmail_watch.interval_s`
   (at least 300), `vips` (addresses or `@domains` that always matter), `topics` (words about what you are working on), `accounts`.

## What it reads, and how little

- Per account it asks Gmail only for **what changed since the last look** (a history position it remembers), not the mailbox. The first
  look only notes where the mailbox is, so old mail is never announced.
- Mail Gmail itself filed as promotions, social, forums, spam, drafts or sent is not downloaded. For the rest it fetches the **headers
  and a short snippet** (sender, subject, labels, a few headers), at most 20 per round; any beyond that wait for the next round.
- Every message is handled once. A new sender is looked up once (a search for mail you sent them, remembered for 30 days).
- If Composio or Gmail fails it waits longer each time and, after three failures, says so once.
- It only reads. The Composio steps it sends must each pass the same check as everything else (`composio_rules`, class 1), and the code
  that builds them has no way to send, label or delete.

## Does it matter? (`mailscore.py`)

Plain checks, not a model's score. Signals add up and each one is named in the notice ("why"):

| Raises it | Lowers it |
| --- | --- |
| a deadline or time pressure; a request or question; money, an invoice or a contract; a security notice; a meeting change; your own `topics` | bulk or automatic mail; promotions, social and forums; receipts and order updates; one-time codes |
| who it is from, but only to **amplify**: a VIP, someone you have written to, a thread you are in, addressed to you, Gmail's own "important" | spam |

Who sent it never makes a message matter by itself ("sounds good, thanks" from your boss is not news), and a stranger's message with a
deadline and a contract can be urgent. If a message that asks for money or action comes from a sender whose own checks (SPF, DKIM,
DMARC) failed, it is capped at "notable" and says it cannot be verified. A result that is close to a threshold and not well supported
is below `gmail_watch.min_confidence` is only "notable". What a message says is never given to a model and never followed: the recommended next
step (reply, decide, pay, check it is real, read) comes from the rules.

## Who is told, how and when (`notifier.py`)

Every proactive notice goes through one layer. Severity decides where it goes:

| Severity | Where |
| --- | --- |
| info | recorded, nobody told |
| notable | the screen and the next digest |
| important | the screen, the voice and your phone (Telegram) |
| urgent | the same; and it may break the quiet hours only if you turn on `notify.urgent_bypass_quiet` |

- **No repeats.** The same news is told once. A new fact (a reply with a new date) or a higher severity is news again, said as an update.
  An urgent notice nobody dealt with is said again after `notify.remind_after_h` hours, at most `max_reminders` times; say "I dealt with it"
  (`notices_resolve`) and it stops.
- **Quiet hours** follow the proactive night window. Whatever would make a sound waits, and the screen and digest still get it.
- **A burst is not a flood:** at most `max_important_per_hour` important and `max_urgent_per_hour` urgent a hour; the rest go to the digest.
- **Private detail stays here.** A notice about mail carries a summary that is shown and said on this computer; the phone gets the title
  only ("details are on the laptop"), and a subject with a sensitive word (bank, salary, loan ...) is left out of it.
- **Delivery that fails is retried** (after 1 minute, 5, 30) and then given up on, not hammered.
- Links and hidden characters are stripped from everything.
- `notices_recent` lists what Minty told you; `notices_status` says whether the watcher is working. "Stop doing things by yourself"
  (`proactive.enabled` off) silences the voice and the phone and leaves the screen.

There is **no phone-call channel**: nothing here can place a call, and none is faked.

## What was and was not checked

Checked: the scoring on realistic messages, the watcher against a scripted source (first look, bursts, expiry, failure and backoff,
two accounts), the notifier's rules, and the Composio response shapes against live read-only answers from your own Composio account
(profile, history, message headers, account list). **Not checked:** a full unattended day, Gmail history expiry on a real mailbox, the quality of the scoring on your own
mail (tune `vips` and `topics`), and delivery to a real phone from this feature.
