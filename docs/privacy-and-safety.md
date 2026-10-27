# Privacy and safety

## What stays on your machine

Everything Minty keeps lives in `~/.config/minty` and `~/.local/share/minty`: settings, keys, memory notes, traces, voices.
Nothing is sent to the project's authors; there is no telemetry.

## What leaves your machine

| To | What | When |
|---|---|---|
| The model provider you chose | the conversation so far, tool results needed for the answer | every turn that needs a model |
| Groq (if keyed) | audio of what you said, for speech recognition | voice turns, and **all the time while "note for me" is on**, unless you chose local recognition |
| Telegram | your messages and Minty's replies | only if you set up the phone step |
| Websites | what the browser or web tools fetch | only when asked to |
| GitHub | one request for the list of releases (no personal data; your IP address is visible to GitHub as usual) | once a day, unless `update.check = false` |
| The address your music player gives for a cover | one picture download (public addresses only, 3 MB cap) | while music plays, for the terminal display |

**Private turns** (anything the leak guard or you mark sensitive) go only to providers marked safe for them. **Privacy mode**
(setup step 5, or `safety.privacy_mode`) goes further: only providers that don't train on your data, and no web tools.

## The leak guard

Before text leaves the machine, Minty scans for API keys, tokens, card and ID numbers, e-mail addresses and phone numbers and masks
or blocks them. Memory refuses to store passwords, PINs, one-time codes and keys.

## Who is in charge

- Every tool has a **risk class**. The safety level you pick (careful, balanced, trusting) decides how much it asks about; money,
  credentials and permanent deletion are always asked about.
- Approvals can be given in the terminal, the panel or Telegram, and are recorded.
- Web pages, files, e-mails and tool results are **data**. If one tells Minty to do something, Minty shows you the text and asks.
- The browser stops at CAPTCHAs and waits for you; it does not try to solve them or to hide that it is automated.
- Only Telegram chats paired with a one-time code are answered. The local API and the panel listen on `127.0.0.1` only.
- **Safe mode** (`minty --safe`, or say "enter safe mode") starts only the basic tools.

## The terminal

Minty can run a command. The terminal is **on by default** (switch it off in Settings: "Let Minty use the terminal", or
`shell.enabled = false`). It is the most powerful thing Minty can do, so it has its own rules:

- A few read-only commands run at once, without asking: `pwd`, `ls`, `whoami`, `date`, `uname`, `uptime`, `df`, `free`, `hostname`,
  `which`, and `git status`, `git log`, `git diff`, `git branch`. Each is checked word by word and run without a shell; one special
  character (`; & | < > $` or a back-tick) is enough to send a command to the question instead.
- **Web search from the terminal** uses `ddgr` when it is installed: `ddgr --json -n 5 words` prints results and runs unasked, like
  Minty's other web search; `ddgr -j words` opens the top result in your browser, which counts as a reversible change (asked in
  the careful safety level only). Other `ddgr` options (a proxy, unsafe search ...) are asked about. Opening any other address
  (`xdg-open URL`) is asked about, because the address usually came from a web page and not from you. Both searches contact
  DuckDuckGo, so privacy mode refuses them.
- **Instagram (`instagram-cli`) and other tools that use your login or your messages are always asked about**, with the command
  read out. Minty never types a password for you: you log in yourself (`instagram-cli auth login`). It is an unofficial client, and
  Instagram can lock an account that uses one, so think before you point it at an account you care about.
- **Every other command is asked about every time, with the exact command read out, whatever safety level you picked.** This is
  the real protection. Minty also refuses `sudo`, `su` and the like before it even asks, but that is only a convenience: a command
  can always be written another way, which is why nothing runs unapproved.
- A command runs in its own process group with none of your keys in its environment, no keyboard input, a time limit
  (`shell.timeout_s`, 30 seconds) and a cap on what it can print (the start and end are kept; anything that looks like a key is
  hidden). When it stops, or when you speak over Minty, everything it started is stopped too.
- Its output is **data**, never instructions, like a web page.
- Background helpers never get the terminal, lockdown stops it, and in privacy mode only the read-only commands above run.
- Read the command before you say yes. Minty cannot tell you that a command is safe, only what it says.

## Your keys

`~/.config/minty/secrets.toml` is created with mode 0600. Keys are typed hidden, never printed and never written to logs or
traces. If you ever paste one somewhere public, rotate it at the provider. Never commit `secrets.toml`; the repository's
`.gitignore` already excludes it.

## Not a medical, legal or financial adviser

Minty can be wrong. For health, money, law or safety, check with a person who knows. If you or someone you know is in danger,
contact local emergency services or a crisis line in your country.


## Handing work over

Missions, coding-agent jobs and the outbox are described in [work.md](work.md). The safety points in short: a mission runs under the
same rules, receipts and secret checks as a conversation, with its own record of what it read, and never gets the terminal; every
step that needs your yes waits for it and no answer is a no; coding agents run in a project folder you choose, are told not to push
or rewrite history, and are judged by the files and tests; e-mail is sent only under rules in settings that no tool can change, a
reply goes only to the person who wrote, every send is recorded, and what may not have been sent is reported as unknown rather
than sent.
