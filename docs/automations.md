# Automations

Minty can make things happen by themselves in three ways. Start with the simplest that does the job.

## 1. Minty's own: reminders, timers, the morning brief, routines

- **Reminders and timers:** "remind me at 6 pm to take my medicine", "set a timer for ten minutes". Reminders survive a restart.
- **The morning brief** (if you allow it): weather, reminders, homework and mail, said once each morning.
- **Routines** (the `learn` plugin): after a browser job worked, Minty can remember how it was done. A routine goes through a draft, a
  test run and your approval before it can be replayed without thinking, and a routine that starts failing is set aside, never
  silently rewritten.

## 2. n8n: "when this happens, do that"

[n8n](https://n8n.io) connects services (Telegram, e-mail, web addresses, Slack and many more) on a schedule or when something
arrives. Minty can build and manage n8n workflows for you by talking to your own n8n. The plugin is on by default, but it needs an
n8n to talk to.

**Set it up (once):**

```bash
docker compose -f docker-compose.n8n.yml up -d     # n8n on http://127.0.0.1:5678, this computer only
```

1. Open http://127.0.0.1:5678. n8n asks you to create **its own owner account** (yours; Minty never creates or sees it).
2. In n8n: **Settings, n8n API, create an API key**.
3. Give the key to Minty (it asks without showing what you type): `minty set-key plugin.n8n.api_key`
4. n8n on another address or port? Set it in Minty's Settings, Plugins, n8n ("n8n address").

Then ask in plain words: "make an n8n workflow that sends me the weather on Telegram every morning at 8", "what did my workflows
do overnight", "turn off the morning one".

**What keeps it safe**

- A workflow Minty creates is **inactive**. It does nothing until you approve activating it, and activating asks you every time.
- Triggering a webhook asks first. Deleting a workflow is always asked, whatever your safety level.
- Minty's n8n key is sent only to your n8n's address, never through a proxy and never followed to another address.
- `docker-compose.n8n.yml` turns off n8n's usage reports and update checks, and publishes it on 127.0.0.1 only. It holds no keys.
- Your workflows live in the `n8n-data` Docker volume: stopping n8n (`docker compose -f docker-compose.n8n.yml down`) keeps them.

> Status: the plugin is tested against a pretend n8n server, and the compose file has been checked for syntax. It has **not** been run
> against a live n8n yet, so expect to look over the first workflow Minty builds before you activate it.

## 3. The terminal: scripts and schedules on this computer

With the terminal on (the default), Minty can write a small script and set it to run on a schedule (a `cron` entry or a systemd user
timer). Be careful with this one: **a schedule made this way lasts until you remove it.** Every command is asked about with the exact
text read out, and Minty is told to show you the file and the commands first and to say how to undo them. To undo:

```bash
crontab -e                                   # delete the line
systemctl --user disable --now NAME.timer    # a systemd user timer
```

If n8n or a reminder can do the job, prefer them: they are easier to see, pause and undo.
