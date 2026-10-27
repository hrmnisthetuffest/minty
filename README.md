<div align="center">

```
███╗   ███╗██╗███╗   ██╗████████╗██╗   ██╗
████╗ ████║██║████╗  ██║╚══██╔══╝╚██╗ ██╔╝
██╔████╔██║██║██╔██╗ ██║   ██║    ╚████╔╝
██║╚██╔╝██║██║██║╚██╗██║   ██║     ╚██╔╝
██║ ╚═╝ ██║██║██║ ╚████║   ██║      ██║
╚═╝     ╚═╝╚═╝╚═╝  ╚═══╝   ╚═╝      ╚═╝
```

**Your voice assistant, on your own computer.**
Talk to it, type to it, or message it from your phone. It keeps your data on your machine, works with the AI providers you choose, and asks before it does anything that matters.

[Quick start](#quick-start) · [Docker](docs/docker.md) · [What it does](#what-it-does) · [Safety](#safety-by-design) · [Docs](docs/getting-started.md) · [Contributing](CONTRIBUTING.md)

![status](https://img.shields.io/badge/status-alpha-orange) ![license](https://img.shields.io/badge/license-MIT-blue) ![python](https://img.shields.io/badge/python-3.10%2B-blue) ![platform](https://img.shields.io/badge/platform-Linux-informational)

</div>

> **Alpha.** Minty is built and used daily on Linux. Text chat, the panel, Telegram and the tools are the
> most tested parts. Voice depends on your microphone, room and CPU. macOS and Windows are untested. Expect rough edges and please
> report them in the issue tracker.

## Quick start

Prefer a container? `docker compose up -d --build`, then open http://127.0.0.1:7778 ([Docker guide](docs/docker.md)).

```bash
git clone https://github.com/hrmnisthetuffest/minty.git
cd minty
./install.sh
```

The installer makes a private Python environment, installs Minty, puts a `minty` command in `~/.local/bin` and starts the
**guided setup**. It never uses `sudo`.

The setup asks one thing at a time. Press Enter to skip anything, `Ctrl+C` to stop and carry on later:

| Step | What happens |
|---|---|
| **1 · Check this computer** | Python, Linux, voice libraries, ffmpeg, Claude Code, a browser. Nothing is changed. |
| **2 · Choose a brain** | Free providers (Groq, Gemini, OpenRouter, Cerebras, Mistral, NVIDIA) with a link to each free key, or your Claude Code login. Keys are typed hidden and checked live. |
| **3 · Voice** | Out loud or typed. Offers the voice libraries and a Piper or Kokoro voice, tells you the size, downloads only if you say yes. Microphone test. |
| **4 · You and your assistant** | What to call you, a personality, how chatty it is. |
| **5 · Safety** | How much it asks before acting, and an optional Privacy mode. |
| **6 · Your phone** | Optional: your own Telegram bot, paired with a one-time code. |
| **7 · Skills and extras** | Switch on the skills you want; add browsing, encrypted backups or PDF reading. |
| **8 · Test and start** | A real "say hello" through your brain, then start Minty (and optionally at every login). |

Run one step again any time: `minty onboard --step voice`. Start Minty with `minty`; say **"Minty"** to wake it.

No installer? Everything it does is `python -m venv .venv && .venv/bin/pip install -e . && bin/minty onboard`.

## What it does

- **Talks.** Wake word, barge-in, a small model that decides whether you have finished your sentence, local or cloud speech
  recognition, Piper or Kokoro voices, or Gemini Live for speech-to-speech.
- **Answers quickly.** It starts recognising your words during the pause after you speak, ends the turn early on a plainly finished
  sentence, and starts speaking at the first comma of a long answer. `python scripts/trace_report.py` shows where the time goes.
- **Thinks with the provider you choose.** A router spreads requests over the providers you add, free or paid, watches their limits,
  falls back when one fails and keeps private conversations off providers that may train on them. Optionally uses your **Claude Code login** as a ladder
  (Haiku for most turns, Sonnet for hard ones, Opus only when you ask).
- **Answers the quick things at once.** The time, the date, sums, percentages, tips, unit conversions, dice, the capital of a country,
  an element's symbol, "how much battery is left" and hundreds of other questions are answered **in under a millisecond with no model**,
  and "set a timer for 5 minutes", "volume down" or "pause" run as one tool call. See [docs/instant-answers.md](docs/instant-answers.md).
- **Two buttons.** **M** mutes the microphone (the whole display turns red) and **Space** starts listening without saying the name;
  click them or press the keys, and change the keys in the settings.
- **Updates itself, if you let it.** It checks GitHub once a day and tells you; `minty update` installs after you say yes, and rolls
  back if the new version does not start.
- **Takes notes.** Say "Minty, note for me" and it writes down everything you say, without the wake word, until you say "Minty, stop noting";
  the latest notes are listed on the on-screen HUD.
- **Reaches you anywhere.** A local web panel, a terminal display, and Telegram (text, voice notes, files, photos) through a bot you own.
- **Uses your command line.** With the terminal on (the default), Minty can run commands and prefers a command-line tool when one fits
  (a web search with `ddgr`, GitHub with `gh`, git). A few read-only commands run at once; **every other command is asked first, with the
  exact command read out.** See [privacy and safety](docs/privacy-and-safety.md#the-terminal).
- **Sets up automations.** Reminders, timers and routines of its own, and n8n workflows built for you (created inactive; nothing runs until
  you approve activating it). See [docs/automations.md](docs/automations.md).
- **Takes goals, not just commands.** Hand it an outcome and it works in the background, checks each step against what its tools
  really returned, and reports done, failed or blocked on a question; coding agents are checked by the files and tests, e-mail goes
  out only under rules you write, and it tells you what needs you. See [docs/work.md](docs/work.md).
- **Does things.** More than forty built-in tool groups: a browser agent, files, notes and memory, reminders and timers, Spotify,
  weather, web research with sources, smart-home and n8n hooks, MCP servers, and coding agents (Claude Code, Hermes, OpenClaw) it can
  start and watch for you. See [docs/plugins.md](docs/plugins.md).
- **Looks after you.** A study helper that guides instead of answering, a planner, optional health, money and life-review packs
  that stay on your machine.
- **Explains itself.** Every turn can be traced; `minty prompts-show` prints what would be sent to the model.

## Safety by design

- Every action has a **risk class**. Money, credentials and permanent deletion are *always* asked about, whatever mode you pick.
- **You approve from anywhere**: the terminal, the panel or Telegram. Approvals are recorded.
- A **leak guard** scans for keys and personal numbers before text leaves the machine; web pages and files are treated as
  untrusted data, never as instructions.
- Keys live in one file only you can read (`~/.config/minty/secrets.toml`, mode 0600). They are never printed, logged or committed.
- **Safe mode** starts only the basic tools: `minty --safe`, or say "enter safe mode".
- Browsing never solves CAPTCHAs or hides that it is automated; it stops and waits for you.

Read more in [docs/privacy-and-safety.md](docs/privacy-and-safety.md).

## Everyday commands

```bash
minty                    # start (voice, API and web panel)
minty onboard            # guided setup, or  --step voice  to redo one part
minty set-key groq       # add or replace a key (hidden input)
minty update             # look for a newer release on GitHub and install it (after you say yes)
minty instant "what is 15 percent of 240"   # try a quick answer; plain  minty instant  lists them all
minty set-pin            # a 4 to 8 digit PIN for the web panel (http://127.0.0.1:7778)
minty claude-brain on    # use your Claude Code login as the main brain
minty telegram-pair      # a one-time code to pair your Telegram chat
minty --safe             # only the basic tools, for this run
minty transfer export    # encrypted copy of your settings and memory for another machine
```

## Develop

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q          # thousands of tests; none needs network access or audio hardware
```

Layout: `src/minty_core` (the assistant), `src/minty_panel` (the web panel), `src/minty_core/plugins/builtin/*` (one folder per
tool group), `tests/`, `scripts/`. New to the code? Start with [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Released under the [MIT license](LICENSE). Third-party packages keep their own licenses; optional voice engines (for example
Piper) have their own terms. Model providers keep their own terms, and those apply to anything you send them.
