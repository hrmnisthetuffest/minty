# Getting started

## 1. Install

```bash
git clone https://github.com/hrmnisthetuffest/minty.git
cd minty
./install.sh
```

You need Python 3.10 or newer. Install Python, its `venv` module and git with your package manager
(for example `sudo apt install python3 python3-venv git`, or `sudo pacman -S python git`). The installer creates `.venv` inside the checkout, links `~/.local/bin/minty`, and starts the setup.
Use `./install.sh --no-onboard` to install without starting the setup.

Doing it by hand:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
bin/minty onboard
```

## 2. The guided setup

`minty onboard` has eight short steps (see the table in the [README](../README.md)). Rules it follows:

- **One question at a time**, with arrow keys and Enter (or numbers when your terminal can't do arrow keys).
- **Skip anything** with Enter. Nothing is required.
- **Nothing is downloaded or installed without asking**, and it tells you the size first.
- **Stop and come back.** `Ctrl+C` stops safely; the next `minty onboard` offers to continue where you were.
- **Redo a part**: `minty onboard --step brain` (steps: `system`, `brain`, `voice`, `you`, `safety`, `phone`, `extras`, `finish`).

Your answers are ordinary settings. You can change every one later in the web panel or in `~/.config/minty/config.toml`.

### Get a free key (about two minutes)

The easiest brain is **Groq**: sign in at <https://console.groq.com/keys>, create a key and paste it when the setup asks (it is
hidden as you type). Groq also does the speech recognition, so one key covers thinking and listening. Other free providers are
listed in [providers.md](providers.md). You can add or replace a key any time with `minty set-key groq`.

## 3. Use it

```bash
minty                # starts the voice assistant, the local API and the web panel
```

- **Voice:** say "Minty", then ask. Say "stop" while it talks to interrupt it.
- **Web panel:** <http://127.0.0.1:7778>. Set its PIN first with `minty set-pin`.
- **Telegram:** after the setup's phone step, run `minty telegram-pair` and send the printed `/pair CODE` to your bot.
- **Text from a terminal:**

  ```bash
  curl -s -H "Authorization: Bearer $(cat ~/.config/minty/api_token)" -H 'content-type: application/json' \
       -d '{"text":"what time is it"}' http://127.0.0.1:7777/ask
  ```

### Make it fit your computer

Minty looks and works the same on every computer; on a slower one it just takes longer and the display redraws less often. `minty tune`
says what kind of computer this is (processor, memory, disk, graphics) and how Minty adapts, and changes nothing. If it still feels
slow, `minty tune --lite --apply` switches to a simpler profile (the light voice and recogniser, no live captions, a still display, no
terminal pictures) after asking, and `minty tune --full --apply` goes back. `minty tune --system` adds operating-system tips such as
compressed swap. The reasoning and the plan for making everything faster are in [performance-plan.md](performance-plan.md).

### The terminal display and album covers

`minty` fills the terminal with a calm display meant to stay open on a second screen: state, a clock, grouped status, the
conversation and a music card. In a terminal that can show pictures (GNOME Terminal and other VTE terminals via sixel; kitty,
WezTerm and Ghostty via the kitty protocol) the card shows the album cover as a real picture; elsewhere it uses a pixel-art cover
made of half-block characters. The card is tall and narrow (cover on top, title and progress under it), and the headings, clock and
progress bar take the colour of the cover. Minty asks the terminal which it speaks, once at start. Override with `terminal_images` in
`config.toml` (`"auto"`, `"off"`, `"sixel"`, `"kitty"`) or the environment (`MINTY_IMAGES=off`). Anything that goes wrong (a terminal that cannot show pictures, one that stops answering) falls back to the pixel-block
cover by itself, for that run. **Best terminal for the real cover: kitty** (`sudo pacman -S kitty` on Arch, `sudo apt install kitty`
on Debian, Ubuntu and Mint): GPU-fast, its own picture protocol, X11 and Wayland. WezTerm and Ghostty are good alternatives. Check
what a terminal can do with `python scripts/term_check.py`. Preview it with sample data:
`python scripts/tui_preview.py --live`. Covers need `ffmpeg` and a player that reports its cover (Spotify, Firefox, VLC and most
others do through MPRIS).

### The chat bar

The bar at the bottom of the display shows your words as you speak them and Minty's words as it speaks. You can also type in it:
press Enter to send, Backspace to correct, Ctrl+U to clear the line. A typed line is answered as text (it is not spoken) and
appears in the conversation; if Minty is in the middle of speaking, ask again in a moment.

### Mute and Listen buttons

Two buttons sit on the top edge of the chat bar: **M Mute** and **Space Listen**.

- **Mute** (key `M`, or click it) turns the microphone off and on. While it is off the whole display turns red and the button says
  *Muted*; press it again to turn the microphone back on.
- **Listen** (key `Space`, or click it) starts listening at once (the display goes straight to LISTENING, with no spoken "Yes, sir?"), the same as saying "Minty". Saying the name still works. If Minty is
  in the middle of an answer, Listen interrupts it and listens.
- The keys work when nothing is typed. To type a message that starts with `M` or a space, press **Enter** first (Enter or Backspace
  on the empty line closes it again); any other letter just starts typing as before.
- Change the keys in the panel (Settings, Display & keys) or with `terminal_mute_key` and `terminal_listen_key` in
  `config.toml`: one character, or the word `space`. `terminal_hotkeys = false` turns the keys off; `terminal_mouse = false` turns
  clicking off (the mouse then selects text as usual).
- The panel's dashboard has the same two buttons, and the local API has `POST /mute` and `POST /listen`.

### Music, rooms and microphones

- **Music while it listens.** The moment Minty starts listening (you said the name, or pressed Listen) everything else that is playing
  is turned down to a tenth of its volume, and back up when the conversation ends, so a song is not heard as your words. It works for
  any player, not only ones Minty can pause (a browser tab, a video). If Minty is stopped or killed while the sound is down, the next
  start turns it back up. Change how far it goes with `audio.listen_duck_level` (0.1 = a tenth, 0 = silent) or turn it off with
  `audio.listen_duck = false`. (Playing media is also paused for the conversation, as before, with `audio.music_pause`.)
- **The room and the microphone.** Microphones and rooms differ, so the volume that counts as speech is not a fixed number: Minty
  learns the background level and sets the bar a little above it (`audio.auto_sensitivity`, on). It drops quickly when the room gets
  quieter, so turning the music down does not leave it deaf. If a quiet microphone still misses you, raise **Microphone sensitivity**
  (`audio.mic_sensitivity`, 1 = normal, try 2 or 3); if a sensitive one reacts to the room, lower it (0.5). Both are in the panel
  (Settings, Microphone & music). With `auto_sensitivity = false` the old fixed `speech_rms_threshold` is used.

### Chrome and searching

The browser tool opens **DuckDuckGo** wherever Google's home page or web search would be used (and `browser_search` defaults to it),
because Google shows a captcha to an automated browser straight away and DuckDuckGo did not in testing. Google Maps, Mail and Docs are
unaffected. Turn **Open DuckDuckGo instead of Google** off in the browser plugin's settings (Plugins page) to go back.

### The control panel and its settings

Open the panel in a browser (`minty-panel`, `http://127.0.0.1:7778`). **Settings** holds everything that can be set, in groups, with a filter box;
press **Ctrl+K** (Cmd+K on a Mac) anywhere to search all pages and settings and jump straight to one. The panel turns red while the microphone is
muted. **Display & keys** has the 24-hour clock switch (`clock_24h`), the mute and listen keys and the mouse buttons.

### Your profile: 500 questions

The **Profile** page of the panel asks 500 fixed questions about you (basics, home, routine, health, fitness, food, sleep, school and work,
interests, devices, how you like Minty, people, money, goals, limits). Answer one at a time or pick from the list, in any order; each can
be skipped and any answer can be changed or cleared. The answers are kept in one private file on this computer (`profile/answers.json`).
When a request is related to some of them (a dinner question and your diet, say), those answers are added to what the model is given,
together with the basics (about you, how you like Minty, your limits), within `profile.max_chars` characters. Turn the sharing off with
`profile.enabled` (Settings, Profile); the answers then stay stored and unused. **Download them** from the page at any time.

### Quick answers and commands (no waiting)

Questions that need no thinking are answered the moment you finish speaking, by Minty itself, with no model call and no internet:
"what time is it", "what is 15 percent of 240", "convert 5 miles to kilometres", "roll two dice", "capital of Japan", "how much
battery is left", "what song is this", "say that again". A few commands also skip the model: "set a timer for 5 minutes", "remind me
in an hour to drink water", "volume up", "pause". The whole list is in [instant-answers.md](instant-answers.md); try one without
speaking with `minty instant "your phrase"`. A request is answered this way only when all of it is a known question, so anything
longer or different still goes to the model. Settings: `instant.enabled`, `instant.actions`, and `instant.skip` (categories to leave
to the model).

### Faster replies, switched on by you

Five more speed-ups exist and are all **off** until you turn them on (Settings, Speed and Voice), so nothing changes by itself. Try one, listen, keep it or switch it off:

- **Warm the connection when the name is heard** (`router.warm_on_wake`): opens the link to the model provider early, so the first request does not wait for the handshake.
- **Steady prompt order** (`turn.stable_prefix`): the notes you change go after everything that stays the same, so providers that reuse the start of a prompt can.
- **Lean prompt** (`turn.lean_prompt`): the short wording of the fixed rules. It has not been compared against the normal wording, so judge it by ear.
- **Sentences made into speech at once** (`tts.parallel`, 1 to 3): the next sentence starts while the last is still being made; the order never changes.
- **Size the local recogniser to this computer** (`stt.auto_model`): picks tiny, base or small from memory, processor and GPU.
- **Let a loud voice interrupt the live voice** (`audio.live_barge_in`, with `live_barge_in_rms`): with the echo guard on, a voice clearly louder than the echo reaches Gemini so you can cut in.

### More you can switch on

Also **off** until you turn them on, with a judge-by-ear rule like the ones above:

- **Carry on after a quick command** (`voice.resume_after_reflex`): say "Minty, volume down", "mute" or "pause" over an answer and the command is done, then the rest of the answer is said again from the sentence that was cut off. Only quick, reversible commands, within 45 seconds.
- **Summarise older conversation** (`turn.fold_history`): turns that fall out of the short window are written up in a few sentences by a model, in the background, every few turns (`turn.fold_every_turns`), instead of being forgotten. Only what was said and answered is sent, with keys hidden; a private turn is summarised only through the private route; the summary stays in memory and starting fresh forgets it.
- **The terminal** (`shell.enabled`): see [privacy and safety](privacy-and-safety.md#the-terminal) before you switch it on.

On by default and switchable off: **Understand a command with a spelling slip** (`instant.fuzzy`), **Correct a claim that did not happen** (`turn.check_claims`) and **Run several lookups at once** (`turn.parallel_reads`).

### Faster replies

Three things make the wait after you stop speaking shorter, all adjustable in the panel's Settings:

- `audio.speculative_stt` (on): while you pause, the words so far are already being recognised, so they are ready when the pause ends.
- `audio.early_finish_ms` (600): a sentence that is plainly finished (it ends with a full stop or question mark and does not trail
  off) ends your turn after 600 ms of quiet instead of the usual `audio.silence_ms` (900). If it ever cuts you off mid-thought, set it to 0.
- `turn.first_clause_words` (8): Minty starts speaking at the first comma of a long first sentence instead of waiting for the whole sentence.

`router.fast_reasoning = "low"` (off by default) also asks reasoning models for less deliberation on simple questions. See where the
time goes with `python scripts/trace_report.py`.

### Take notes by voice

Say **"Minty, note for me"**. Minty starts listening and writes down everything you say, with no wake word and no answers, until you
say **"Minty, stop noting"** (the whole phrase). Silence does not end it. Each note is saved the moment it is heard, in
`~/.config/minty/voice-notes.md` (one dated heading per session), and the latest notes are listed at the bottom of the on-screen HUD
while you work in other windows (turn the HUD on with `overlay.hud`; `voice.notes_hud_lines` sets how many show, `voice.notes = false`
turns the feature off). Note: with the default cloud speech recognition everything you say while noting is sent to Groq; set
`stt.provider = "local"` to keep it on your computer.

Keep it running in the background and start it at login (Linux, systemd): `scripts/install_service.sh`.

Docker: see [docker.md](docker.md).

## 4. Updates

Minty can update itself from its GitHub repository.

- Once a day it asks GitHub whether there is a newer release and, if there is, shows a notice on the display (nothing is installed, and
  nothing is spoken). Turn that off with `update.check = false`.
- `minty update` shows what is new and, after you say yes, moves this checkout to the release, installs changed dependencies and checks
  that the new version starts; if it does not, it goes back to the version that worked. `minty update --check` only looks, `--yes`
  skips the question.
- It only ever updates a git checkout (the one `install.sh` makes), from the repository it was cloned from, and never over local
  changes or commits of your own. To update a copy that is not a git checkout, clone again and run `install.sh`.
- `update.auto_install = true` installs a newer release by itself. Code from the internet then runs on your computer without you
  reading it, so use it only for a repository whose owner you trust completely; with `update.verify_signed = true` it also requires a
  signed git tag. It is off by default.
- Which repository: `update.repo` (`owner/name`), or by default the one your checkout's `origin` points at.
- Maintainers: see [releasing.md](releasing.md).

## 5. Where things are

| What | Where |
|---|---|
| Settings | `~/.config/minty/config.toml` |
| Keys (mode 0600, never printed) | `~/.config/minty/secrets.toml` |
| Memory notes (plain text, yours to edit) | `~/.config/minty/memory.md` |
| Notes you took by voice | `~/.config/minty/voice-notes.md` |
| Setup progress | `~/.config/minty/onboard.json` |
| Voices and models | `~/.local/share/minty/voices/` |

Use another folder for everything with `MINTY_CONFIG_DIR=/some/folder minty`.

## 6. Problems

| Symptom | Try |
|---|---|
| `minty: command not found` | add `export PATH="$HOME/.local/bin:$PATH"` to your shell profile |
| No sound or no microphone | `minty onboard --step voice` runs the microphone test; install `libportaudio2` and `ffmpeg` |
| It answers to the music or the TV | lower `audio.listen_duck_level`, or raise `audio.speech_rms_threshold`; lower `audio.mic_sensitivity` for a hot microphone |
| It does not hear a quiet voice | raise `audio.mic_sensitivity` (try 2); keep `audio.auto_sensitivity` on |
| "Replies are slow" | the free tiers have limits; add a second provider key, or use `minty claude-brain on` |
| Only basic tools are available | Minty is in safe mode (`--safe`); start it normally with `minty` |
| Start from scratch | stop Minty and move `~/.config/minty` aside; the next `minty` offers the setup again |

To remove Minty: delete the checkout, `~/.local/bin/minty`, `~/.config/minty` and `~/.local/share/minty`.
