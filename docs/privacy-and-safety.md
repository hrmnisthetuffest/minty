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

## Your keys

`~/.config/minty/secrets.toml` is created with mode 0600. Keys are typed hidden, never printed and never written to logs or
traces. If you ever paste one somewhere public, rotate it at the provider. Never commit `secrets.toml`; the repository's
`.gitignore` already excludes it.

## Not a medical, legal or financial adviser

Minty can be wrong. For health, money, law or safety, check with a person who knows. If you or someone you know is in danger,
contact local emergency services or a crisis line in your country.
