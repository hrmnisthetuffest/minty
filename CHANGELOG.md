# Changelog

Every change, with the version it shipped in. **Every change bumps the alpha number** (`1.0.0-alpha.1`, `.2`, ...) in
`src/minty_core/__init__.py` and `pyproject.toml` (`1.0.0a1`, `a2`, ...) and adds an entry here; `tests/test_version.py` checks
that the two versions agree and that this file has a heading for the current one. The maintainer moves from alpha to beta by hand,
when it is good enough.

## 1.0.0-alpha.1 (2026-10-19, first public release)

### Guided setup
- `minty onboard`: an eight-step, one-question-at-a-time setup with a banner, progress dots, arrow-key menus, hidden key
  entry and live key checks (`src/minty_core/onboard/`). Steps: check this computer, choose a brain, voice, you and your
  assistant, safety, your phone (Telegram), skills and extras, test and start.
- It resumes where it stopped (`~/.config/minty/onboard.json`), can run one step (`--step NAME`) or start again (`--again`), and
  never downloads or installs anything without asking and stating the size.
- `minty setup` is an alias for `minty onboard`. The first plain `minty` in a terminal on a clean install offers the setup
  (`MINTY_NO_ONBOARD=1` skips it).
- Install hints in the setup come from the `system/osinfo.py` tables (a test forbids hard-coded `apt` commands).
- Tests: `tests/test_onboard.py` (20 tests: whole run, resume, single step, old Python, Claude on/off, voice installs and
  downloads, failed download, Telegram good and bad token, extras, the terminal UI with arrow keys and without a terminal).

### Install and project files
- `install.sh` (private `.venv`, `minty` link in `~/.local/bin`, then the setup; no `sudo`; `--no-onboard`).
- `LICENSE` (MIT), `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, issue and pull request templates, a CI
  workflow (`.github/workflows/ci.yml`, Python 3.12), `docs/getting-started.md`, `docs/providers.md`, `docs/privacy-and-safety.md`.
- `.gitignore` excludes keys, tokens, `config.toml`, `memory.md` and `*.enc`.
- `minty --version` and the version in the terminal title; `pyproject.toml` metadata.

### Privacy
- The speech test fixtures (`tests/fixtures/speech_weather.wav`, `speech_stop.wav`) are synthesized with the Kokoro model
  (Apache-2.0), saying "What is the weather like today?" and "Stop.".
- Test data uses made-up names, places and e-mail addresses only; the tree was scanned for key-shaped strings (`gsk_`, `sk-`,
  `AIza`, Telegram tokens, SSH keys, private-key headers).

### Known gaps
- Internal work-package labels (`WP-16`, "Wave 16") remain in some docstrings; they are harmless history.

## 1.0.0-alpha.2 (2026-10-19)

### Added: "note for me" (noting mode)
- Saying **"Minty, note for me"** (also "start noting", "take notes for me", "noting mode") starts noting mode: Minty listens with
  no wake word and writes down every utterance, never answering and never sending it to the assistant, until you say the whole
  phrase **"Minty, stop noting"** (also "stop taking notes"). Silence does not end it. Works in classic and in Gemini Live mode
  (Live hands over after its one reply). Filler such as "um" is not noted; a long sentence that merely contains the phrase is a note.
- Notes are saved the moment they are heard in `~/.config/minty/voice-notes.md` (private file, a dated heading per session).
- The HUD lists the latest notes as its last block, at the bottom of the window ("✎ Noting" and a bullet per note), so a card
  placed below it (a now-playing card) stays under the list. New bus event `notes`, new overlay fields `noting` and `notes`.
- Settings: `voice.notes` (on) and `voice.notes_hud_lines` (6). Spoken confirmation never contains the stop phrase, so a
  microphone that hears it cannot end the mode by accident.
- Code: `voice/notes.py` (phrases and `NoteTaker`), `VoiceExtras.intercept` (`Intercept.start_noting`), `VoicePipeline._note_loop`,
  `LiveMode.start_noting`, `OverlayFeed`, `OverlayModel.Frame`, `HudWindow`. Tests: `tests/test_voice_notes.py` (23 tests) and two
  in `tests/test_live.py`.
- Docs: `docs/getting-started.md` (voice notes, where the file is), `docs/privacy-and-safety.md` (everything said while noting goes
  to the cloud recogniser unless `stt.provider = "local"`), README.
- Not included: the now-playing "music card" itself does not exist in this repository (it was only proposed); the notes block is
  placed so it can sit above it.

## 1.0.0-alpha.3 (2026-10-19)

### Changed: the terminal display, redesigned to be left open all day (from your sketch)
- **Title**: the release stage is red (`Minty v1.0.0-`**alpha.3**), and turns into "beta" the day you change the version.
- **Bottom red line removed.** A clean start-up self-check is no longer shown at all; a real problem or an error appears for 12
  seconds inside the header panel, then fades.
- **Status panel reorganised** into three cards under accent headings: BRAIN (model, thinking, brain, capacity bar), VOICE (mode,
  speech, mic, music) and TODAY (day, private, working, memory). Two columns on narrower screens, one on narrow ones; rounded
  borders; the "say switch to ..." line stays below.
- **Music card** in the Conversation panel, on the right: title, artist, album, progress bar with times, an equaliser while it
  plays, "paused", and "Nothing playing" when idle. Dropped below 92 columns so the conversation never loses space.
- **Header clock and date** on screens 100 columns wide or more (large light digits), for a second display or a laptop on the side.
- **Conversation** wraps under its label ("You" / "Minty") and shows the newest turns that fit; while "note for me" is on, the panel
  becomes "Noting" and lists the notes.
- Code: `tui.py` (`title_text`, `clock_block`, `music_card`, `conversation`, grouped `chips`, measured `status_rows`),
  `audio/music.py` (`MusicWatcher.now_playing`: title, artist, album, position and length from `playerctl`, cached, never blocks,
  hung players skipped), `statusline.snapshot` (`now_playing`). The status "MEMORY" row no longer lists custom commands.
- New `scripts/tui_preview.py` draws the display with sample data at any size (`--svg out.svg`, `--live`, `--noting`, `--paused`).
- Tests: 10 new in `tests/test_tui.py`, 4 in `tests/test_music.py`, 1 in `tests/test_statusline.py`. Two older tests were updated to the
  new wording (`tests/test_model_switch.py`: "★" marks your pick and "(x unavailable)"; `tests/test_brownout.py`: the capacity bar,
  with the call count or, when running low, the time it runs out, on the row below).

## 1.0.0-alpha.4 (2026-10-19)

### Added: the album cover in the music card
- The music card now shows the album cover (12 by 12 pixels, drawn in half-block cells, so it works in any terminal) to the left of
  the title, artist and equaliser; the progress bar and times run under both. The card is 36 columns wide and is dropped below 96.
- The picture's address comes from the media player (`mpris:artUrl`). A web address goes through the same public-address check
  as every other fetch (a player cannot point Minty at localhost or your network), only `image/*` answers are used and the download
  is capped at 3 MB; a local file must be an image (png, jpg, webp, gif, bmp) of at most 5 MB. ffmpeg (already used for voice notes)
  crops it square and shrinks it, so there is no new Python dependency. Without ffmpeg, while it loads, or when it fails (retried
  after a minute), a steady coloured tile with a note on it is shown, a different colour per track. The display never waits for it.
- Code: `audio/cover.py` (`art_bytes`, `decode`, `to_text`, `tile`, `CoverCache`), `MusicWatcher.now_playing` adds `art_url`,
  `TuiModel.covers`, `tui.music_card`. `scripts/tui_preview.py` draws a sample cover.
- Tests: `tests/test_cover.py` (10, including a real ffmpeg run on a generated PNG), plus new cases in `tests/test_tui.py` and
  `tests/test_music.py`.

## 1.0.0-alpha.5 (2026-10-19)

### Added: the album cover as a real picture where the terminal can show one
- New `gfx.py`: asks the terminal once which picture protocol it speaks (kitty graphics, or sixel via the device-attributes reply
  that GNOME Terminal and other VTE terminals, foot, xterm, Konsole and Windows Terminal give), reads the cell size in pixels, and
  draws pictures over a block of cells the layout keeps blank. Sixel is painted again after every frame (text paints over it) inside
  one synchronized-output update; a kitty picture is sent once and only moves or changes when it has to. Everything is removed on
  exit. A terminal that speaks neither keeps the half-block cover.
- With pictures on, the cover block is 18 by 9 cells (about 162 by 162 pixels, square) and the card is 40 columns wide (shown from
  100 columns). The cover is cut square and resized by ffmpeg with Lanczos from the full-size file the player gave, so it is a real
  picture, not blocks. While it loads a tile of the same size is shown, so nothing moves when it arrives.
- A purpose-built sixel encoder (numpy, 96 colours, run-length compressed): a 162 by 162 cover is about 16 KB and encodes once, off the
  display's loop, in under 0.3 s; the round trip through a decoder differs from the source by 4 of 255 on average.
- Settings: `terminal_images` = `auto` (default), `off`, `sixel` or `kitty`; `MINTY_IMAGES` in the environment does the same.
  `scripts/tui_preview.py --live [--images ...]` shows it with sample data.
- Not verified: drawing in a real GNOME Terminal, kitty or WezTerm. The detection, the encoders, the placement maths and the cover
  loading are tested; whether your terminal build has sixel switched on is answered by the detection at start.
- Tests: `tests/test_gfx.py` (15), more in `tests/test_cover.py` and `tests/test_tui.py`.

## 1.0.0-alpha.6 (2026-10-19)

### Added: cover size, your own art in the preview, and a terminal check
- New setting `terminal_cover_size` (10 to 36, default 18): the real cover's width in cells, about 9 pixels each, so 28 is about
  256 by 256 pixels. The card widens with it (the cover plus 22 columns) and is only shown when the conversation keeps 60 columns.
- `scripts/tui_preview.py --art FILE_OR_URL --cover-size 28` shows any image of yours as the sample cover. No album art is bundled in
  the repository: the music card shows whatever your player reports for the track that is playing.
- New `scripts/term_check.py`: asks your terminal whether it draws pictures, prints its raw answer and the cell size, and draws a
  test gradient if it can. Useful when Minty shows the block cover instead of a real picture.
- Tests: two more in `tests/test_tui.py`.
- The preview now follows the song that is really playing (`playerctl`, any MPRIS player: Spotify, Firefox, VLC); with no player
  running it keeps the sample so the card can still be previewed (`--sample-music` forces the sample). The real display always follows
  the player. Fixed: the "music: playing" flag and the track come from separate refreshes and could disagree for a moment; a
  playing track now always counts as music (`statusline.snapshot`). A player that gives no cover address (Firefox does not) gets the
  coloured tile.

## 1.0.0-alpha.7 (2026-10-20)

### Added: a failsafe for pictures
- Any error while drawing a picture (a terminal that claimed support and cannot show it, a closed pipe, a broken answer) now turns
  pictures off for that run: the picture is removed, and the display carries on with the pixel-block cover. The synchronized-update
  mode is always closed first, so the terminal is never left frozen. Terminals that do not answer the detection question, or answer
  "no", never get pictures in the first place (this was already so; it is now also tested end to end).
- Docs: kitty is the recommended terminal for the real cover (WezTerm and Ghostty as alternatives); `scripts/term_check.py` explains
  what a terminal can do and that the block cover is expected, not a fault, where pictures are not supported.
- Tests: two more in `tests/test_tui.py` (`safe_paint_images`).

## 1.0.0-alpha.8 (2026-10-20)

### Changed: a tall music card, and the colours follow the song
- **The music card is now vertical**: the square cover on top, then the title (two rows), artist and album, a progress bar with the
  times on one line, and the equaliser. It is as narrow as its cover (at least 22 columns wide) instead of a wide banner, so the
  conversation keeps almost all the width. It is only shown when the conversation keeps 50 columns beside it.
- **The cover size adapts to the height**: the cover is as big as the setting asks (`terminal_cover_size` for real pictures, 18 by 9
  cells for the block cover) and as the rows left allow; a short terminal gets a smaller square cover, a very short one a card
  without a cover, a tiny one no card. The block cover is now 18 by 18 pixels (it was 12 by 12).
- **The accent colour follows the song.** The most vivid area of the cover (greys and near-black do not count) becomes the accent
  of the headings, the clock, "Minty" in the conversation, the progress bar, the equaliser and the card's border, lifted to a
  brightness that reads on a dark screen. A track with no cover gets the colour of its placeholder tile; a grey cover or nothing
  playing keeps the default ember. The colour fades to a new song over about a second (at once with reduced motion). The red
  "alpha" in the title, the state colours and the status colours (green, yellow, red) do not change.
- Code: `audio/cover.py` (`dominant_color`, `tile_accent`, `CoverCache.accent`, covers keyed by size), `tui.py` (`card_plan`,
  vertical `music_card`, `TuiModel.follow_accent`).
- Tests: 8 new in `tests/test_cover.py` and `tests/test_tui.py`; the card layout tests were rewritten for the vertical card.

## 1.0.0-alpha.9 (2026-10-20)

### Changed: a bolder clock, a bigger play icon and equaliser, no player name
- **Clock**: solid block digits, five rows tall with square pixels (2 cells wide, 1 tall), shaded from the song's colour at the top to
  a deeper tone at the bottom; the colon is two squares that blink each second (steady with reduced motion); the day and date
  underneath in small spaced capitals (`M O N · 1 9 · O C T`). It is 30 columns wide and shown from 112 columns.
- **Music card**: the player's name ("spotify") is gone from the card. The play icon is a three-row triangle in the song's colour,
  and the equaliser beside it is three rows tall with moving two-cell bars (flat and still with reduced motion). Paused shows a
  three-row pause icon and the word "paused". The card keeps its shape on short terminals; the cover gives up two rows for the
  bigger icon.
- Tests: the Rich colour tests now read the colours from the SVG export instead of ANSI codes (Rich caches a style's ANSI codes after
  its first render, so a test that used another colour mode earlier made one of them fail in the full suite, not alone); new tests
  for the clock, the icon, the equaliser and the pause icon. The alpha.8 commit contained that one order-dependent test failure;
  this release fixes it.

## 1.0.0-alpha.10 (2026-10-20)

### Changed: a quieter screen, a chat bar, more of the cover's colour (from your annotated screenshot)
- **The Status panel is gone.** In its place one quiet line under the header: the model, the voice mode and the microphone
  (`claude · haiku · classic · mic on`). It only grows when something needs attention: muted, private, privacy mode, deep thinking,
  models resting (`7/9 models ready`), running low (`10% left` and when it runs out), or work in the background. The switch-to
  list, the capacity bar, memory counts, the speech engine and the rest are no longer drawn (the web panel still has them).
- **A chat bar at the bottom.** While you speak, your words appear in it as they are recognised (`you › what is the weather like in
  Paris…`); while Minty speaks, its words appear a few at a time at the pace of the voice and then stay for ten seconds (`Minty ›`);
  idle it says how to use it. **You can also type**: letters appear in the bar, Enter sends the line, Backspace and Ctrl+U edit it,
  arrow keys are ignored, Ctrl+C still stops Minty. A typed line goes through the same path as the local API (command phrases first,
  then the assistant) and is answered as text on the display; if the voice is in the middle of something you are told to ask again.
  (`typed.py`: `typed_chat`, `answer_text`, `command_router`, shared with the API.)
- **The music card**: a strip of the cover's main colours between the picture and the title (tones of the tile colour for a track
  with no cover); the title now sits directly above the artist (no reserved empty row; a long title wraps to two rows).
- **More of the screen follows the cover**: the idle orb glows in the song's colour and the header border takes its tone while
  waiting (listening, speaking, thinking and the other states keep their own colours); the chat bar's border and prompt, the clock,
  headings, progress and equaliser already did.
- Preview: `scripts/tui_preview.py --live` now accepts typing (it answers by repeating the line) so the bar can be tried;
  `--typing "text"` and `--state listening` show a moment in the SVG.
- Tests: 14 new in `tests/test_tui.py`, 3 in `tests/test_cover.py`, `tests/test_typed.py` (5); four tests of the removed Status grid
  were deleted with it.

## 1.0.0-alpha.11 (2026-10-20)

### Changed: the card at the bottom right, a "Coming up" column, a narrower chat (from your annotated screenshot)
- **The music card now sits at the bottom right** of the conversation panel with a little padding (one column on the right and
  left, one row under it), as tall as its content, not stuck to the top. The real-picture overlay follows it.
- **Reminders and more in the free space.** A new "COMING UP" column in the middle of the conversation panel lists your reminders
  and running timers, soonest first: `⏱ 4:32  tea`, `23:59  call dad`, `tmrw 10:32  ...`, `Wed 10:02  ...`. Minty's own nudges are not
  listed. When nothing is due it says so and shows how to ask. The data comes from the scheduler and the timers
  (`statusline.coming_up`, a new `agenda` field in the status snapshot; a failing store gives a shorter list, never an error).
- **The chat takes about half of the panel** (`column_widths`): chat, coming up, card. Below about 100 columns the middle column
  is dropped and the chat takes its width, so nothing is squeezed.
- The title in the header is explicitly white, so it stays readable when the border takes a dim tone of the song's colour.
- The spoken "say switch to ..." list and its test were removed with the Status panel (the voice commands still work).
- Tests: 6 new layout tests in `tests/test_tui.py`, 2 in `tests/test_statusline.py`; `tests/test_model_switch.py` no longer imports the
  removed list. (The alpha.10 changes below were built and tested before this release but never committed on their own.)

## 1.0.0-alpha.12 (2026-10-20)

### Fixed: the accent colour now really is the cover's main colour
- The accent used to be the most *vivid* hue, brightened and made more saturated, which could differ a lot from what the cover
  looks like. It is now **the colour used by the most pixels, as it really is** (the average of that area), **skipping black, white
  and grey**: if the most used colour is white, black or grey, the next most used one is chosen. Pixels are grouped by hue,
  lightness and softness, so a sky blue and a navy are two colours while the tones of one blue are one.
- Only a colour too dark to read on a dark screen is brightened (to a brightness of about 0.62, same hue); soft and light colours
  are left as they are. A picture with almost no colour (under 4% of its pixels, which JPEG noise cannot reach) has no accent and
  the default ember stays.
- The colour strip under the cover uses the same counting: the colours used most first, true to the picture, one per hue.
- Tests: the accent and palette tests in `tests/test_cover.py` were rewritten (most used wins, true colour, next colour when the
  first is white, black or grey, dark colours lifted only as much as needed).

## 1.0.0-alpha.13 (2026-10-20)

### Fixed: first-token latency was never recorded
- The trace writer masked any field whose name contains "token" as a credential, so **every `first_token_ms` in every trace was stored as
  `"…"`** (194 events in seven days) and the trace report could not show a first-token time (it crashed on the text instead).
  The pattern now treats `token` as a credential only when it is not `tokens` (a count) or `..._ms` (a duration); `access_token`,
  `api_token`, `token`, `refresh_token`, keys, secrets, passwords, cookies and authorization stay masked. `trace_report` skips the
  masks in old traces instead of crashing. Tests added to `tests/test_trace.py` and `tests/test_trace_report.py`.

### Added: `docs/performance-plan.md`
- Research (how the Linux kernel community optimises, what makes a conversation feel natural, speech and language models that fit old
  hardware, Python on small machines) with sources, measurements of this code and of seven days of real traces (first spoken word p50
  2.45 s and p95 12 s; 68% of output tokens were hidden reasoning; 2% prompt-cache hits; about 2,500 tokens in every request; a fixed
  900 ms silence wait), fifteen findings, workstreams A to G with expected gain, effort and risk, the order of work, and how each step
  is measured.

### Removed
- The colour strip under the album cover (you asked for it gone); a row of air keeps the picture and the title apart. The palette
  code and its tests went with it.

## 1.0.0-alpha.14 (2026-10-20)

### Added: the first speed tranche (workstreams A, B and D of `docs/performance-plan.md`)
- **`minty tune`**: describes this computer (processor and AVX2, threads, memory and what is free, swap, disk type, graphics cards and
  their memory, battery), puts it in a tier (**t0** very old or small, **t1** ordinary, **t2** modern) and shows the settings that
  suit it next to what they are now. Nothing changes until `minty tune --apply` (it asks; `--yes` does not). `--system` adds
  operating-system tips as words only (zram and swappiness for small memory, a spinning disk, two threads, battery, real-time audio).
  New `hwprobe.py` (reads /proc and /sys under a root folder so tests can describe any machine; asks `nvidia-smi` about an NVIDIA card)
  and `tune.py`. Not knowing something never switches a feature off: an unreadable machine is t1.
- What a t0 machine is set to: the Piper voice, live captions off, still display, no terminal pictures, the tiny local recogniser. A
  machine without AVX2 gets Piper and is told why; a GPU with enough memory gets the larger local recogniser. A modern machine is
  left as it is. A test checks that every recommended setting is a real setting with a valid value.
- **The setup offers it**: the first step shows the computer and, on anything but a modern one, asks whether to use the settings that
  suit it (default yes).
- **Python runtime tuning at start** (`runtime.tune_gc`): after start-up the loaded objects are frozen and the collector's threshold
  is raised, so a steady-state assistant stops rescanning its own settings and plugins. Cycles are still collected; a threshold someone
  already chose is left alone.
- **The service unit sets `MALLOC_ARENA_MAX=2`**: glibc keeps an allocation arena per core by default, which wastes memory on a small
  machine.
- **A circuit breaker for tools** (`toolbreaker.py`): a tool that fails three times in a row is not offered to the model for ten
  minutes, and the model is told it is unavailable. In the traces one failing tool cost a whole model round trip six times. After the
  rest it gets one try; a failure rests it again at once, a success clears the count.
- **Loop-stall traces keep the frame that matters** (`loopwatch.compact_stack`): the stack of a frozen event loop was stored as full
  file paths cut from the end, so on a long install path the innermost frame (the code that was running) was lost; it is now
  `file.py:line function` for the last six frames, dropping the outermost first. This was also the cause of a test that failed on the
  laptop and in a deep folder.
- Tests: `tests/test_hwprobe.py` (15), `tests/test_tune.py` (7), `tests/test_runtime.py` (2), `tests/test_toolbreaker.py` (4), more in
  `tests/test_onboard.py` and `tests/test_service.py`.
- Not done yet from the plan (next): the measurement harness and per-stage latency in the trace (M1), the faster end-of-speech wait,
  speculation, first-chunk voice, the prompt diet and stable prefix, the Live-mode interruption fix. None of the numbers in the plan
  has been re-measured after these changes; this tranche removes costs (idle CPU on small machines, wasted round trips) but does not
  yet change the time from speaking to hearing the answer.

## 1.0.0-alpha.15 (2026-10-20)

### Changed: the same look and features on every computer
- **`minty tune` no longer changes how Minty looks or works.** By default it describes the computer and how Minty adapts to it and
  changes nothing, even with `--apply`. The simpler profile (the light Piper voice, the tiny local recogniser, no live captions, a still
  display, no terminal pictures) is a choice: `minty tune --lite` shows it, `--lite --apply` uses it after asking, `--full --apply` goes
  back to the defaults. The setup's first step only says what kind of computer this is; it no longer offers to change settings.
- **The display adapts its frame rate, not its look.** The delay before the next redraw is now `max(normal delay, frame time / share)`
  with the frame time smoothed: at most a third of a core while something moves and a twentieth while waiting, never slower than one
  frame in two seconds idle (0.4 s busy). On this PC (6 ms frames) nothing changes; on a machine ten times slower it redraws about once
  a second while waiting and about six times a second while active, with the same screen.
- **Live captions adapt too** (`pipeline.caption_every`): the refresh interval is at least twice the time one caption took, so a slow
  recogniser is no longer kept busy back to back while you speak. Captions still appear, a little less often.
- `docs/performance-plan.md`: the principle (same look and features everywhere; speed from cheaper work, not less work), updated work
  items, and a section on whether to rewrite the project in Rust (no; the numbers are there).
- Tests: `tests/test_hwprobe.py`, `tests/test_tune.py`, `tests/test_onboard.py` rewritten for the new rule; new cases in
  `tests/test_tui.py` and `tests/test_captions.py`.

## 1.0.0-alpha.16 (2026-10-20)

### Wake word and names
- **Wake word:** `minty`, with `mintee`, `minti` and `mindy` as common mis-hearings. The close-match rule compares with the name (the
  first phrase) and never accepts real words that sound close ("mint", "mind", "mine", "mini").
- The system prompt says `Your name is "Minty".` File formats start with `MINTYBK1` (backup), `MINTYEX1` (export) and `MINTYPV1`
  (private vault); the Telegram relay envelope starts with `MINTY-RELAY/1`.
- New: extra non-matches in `tests/test_wakewords.py`.

## 1.0.0-alpha.17 (2026-10-20)

### Instant answers (new): the quick things need no model
- **`minty_core.instant`**: requests that are *wholly* a known question are answered by Minty itself, in about a tenth of a
  millisecond, with no model call, no network and no quota. Everything else goes to the model exactly as before. 37 rule groups,
  347 example phrases (each understands many more wordings), listed in [docs/instant-answers.md](docs/instant-answers.md), which is
  written from the rules and checked by a test.
  - **Time and date:** the time (said the way people say it: "noon", "4:32 PM"), the date, the day, the week number; tomorrow,
    yesterday, "in 10 days", "3 weeks ago", "next Friday", Christmas and other fixed holidays; days until or since a date, a
    weekday, the weekend or a holiday; days left in the year or month; "what time will it be in 2 hours", "how long until 6 pm";
    the time in 145 cities, countries and zone names; leap years and the days in a month.
  - **Maths:** sums with words or symbols ("twelve minus five", "2 to the power of 10", "(2 + 3) * 4"), percentages, discounts,
    tips, GST and VAT, splitting a bill, roots, factorials, logs, trigonometry, primes, factors, gcd and lcm, averages, number
    bases, Roman numerals, times tables, areas and volumes. A small safe calculator (no `eval`) does the arithmetic.
  - **Conversions:** 62 units of length, weight, volume, speed, area, time, data, angle and temperature.
  - **Facts that do not change:** 152 capitals, the 118 elements (symbol, number, name, the lighter ones' mass), 28 formulas, 89 full
    forms (CPU, UPI, JEE, ...), constants, counts, planets, "how many lakh in a crore".
  - **This computer:** battery, memory, disk space, processor load and temperature, uptime, address, user, system; what song is
    playing (from the music watcher), "say that again", "what did I just ask".
  - **Chat and games:** hello, thanks, who are you, are you there, the version; a coin, dice, a random number, card or colour,
    "pick between a and b", the magic 8 ball, jokes, facts, quotes, tongue twisters, counting, spelling.
- **Commands without a model** (`instant.actions`): "set a timer for 5 minutes", "how long is left on the timer", "remind me in an
  hour to drink water", "remind me at 6 pm to ...", "volume up/down/40", "mute the sound", "brightness 50", "lock the screen",
  "pause", "resume", "next song", "previous song". Each is one tool call through the usual safety checks; if the tool is not set up
  the request goes to the model, so nothing is lost. The agent's fast path now takes these after the page commands.
- **Safety of the idea:** a rule fires only when the whole request matches, so "what is the time complexity of quicksort", "remind me
  tomorrow at 9", "pick up the kids and the dog" and 30 more look-alikes in the tests go to the model. Text that came from outside
  (a forwarded message) is never answered locally. Praise like "perfect" is not treated as small talk, because it can be a "yes".
- Settings: `instant.enabled`, `instant.actions`, `instant.skip = ["fun and games"]` (categories to leave to the model). They apply
  at the next request. Trace: an `instant` event with the rule that answered.
- `minty instant "phrase"` tries a phrase (exit 0 if answered here), `minty instant` lists everything.
- Text helpers: spoken numbers become digits ("two thousand twenty six" is 2026, "twelve minus five" stays a subtraction); trailing
  "please", "for me", "right now" are ignored; "what's" is "what is"; 1,200 is 1200.
- Also: possessives broken by the rename ("Minty's words") fixed; the Hindi transliteration used in two tests follows the new name.
- Tests: `tests/test_instant_*.py` (about 500 cases), including 36 requests that must *not* be answered locally.

## 1.0.0-alpha.18 (2026-10-20)

### Faster turns: hear the pause, finish early, speak sooner
- **Recognition during the pause (`audio.speculative_stt`, on by default).** After `audio.spec_after_ms` (350 ms) of silence following
  speech, the words so far are recognised in the background. When the pause turns out to be the end of your turn, the words are
  already there, so the recognition time (about 0.3 to 1 s on the cloud or a small local model, longer on an old laptop) is spent
  *inside* the pause instead of after it. If you carry on speaking, the guess is thrown away and nothing is lost (at most two
  guesses a turn). Only the recognition runs early; the model, tools and voice wait for the real end of the turn.
- **A finished sentence ends the turn early (`audio.early_finish_ms`, 600 ms; 0 turns it off).** When the guess is a sentence the
  recogniser closed with a full stop, question mark or exclamation mark, and its last word is not one people stop on ("and", "to",
  "um"), the turn ends after 600 ms of silence instead of 900. Anything doubtful ("so the plan is", "I was going to", a sentence
  with no full stop) still waits the full pause. **This is the one change in this release that you have to judge with your ears:** if
  it ever cuts you off, set `audio.early_finish_ms = 0` (nothing else changes).
- **Captions stop re-reading a silent recording.** During a pause the audio is the same, so the live caption is no longer recognised
  again every second; that CPU goes to the guess instead.
- **The first spoken piece may stop at a comma (`turn.first_clause_words`, 8; 0 = whole sentences only).** A long first sentence no
  longer has to be finished before speech starts: "The capital of France is Paris," is spoken while the rest is still arriving. Only
  the very first piece of an answer is cut, never inside brackets, quotes or numbers like 1,200.
- **`router.fast_reasoning` (off by default; `"low"` or `"medium"`).** Simple spoken turns can ask reasoning models (Groq's gpt-oss)
  for less deliberation, which is faster to the first word. Hard turns, thinking mode and other providers are untouched. It is off
  because answers are slightly less careful and the project's rule is to measure that before changing a default.
- **Measuring (`minty` trace report, milestone M1).** Every classic turn now records `silence_ms` (the pause that ended it) and `spec`
  (`used` or `early`); `scripts/trace_report.py` prints "Where the wait goes (p50)": the pause, the recognition, the time to the
  first sentence, how often the guess was used, and how many answers were instant (no model).
- Tests: `tests/test_speculative_turn.py` (24), `tests/test_first_clause.py`, `tests/test_fast_reasoning.py`, new cases in
  `tests/test_trace_report.py`; `EnergyEndpointer.quiet_ms` is new.

## 1.0.0-alpha.19 (2026-10-20)

### Removed: the school-day mode
- **The day model is gone** (`daymodel.py`): school days and hours, the SCHOOL / HOME mode on the display, the holiday calendar,
  "I'm home today" / "school is off till Monday" / "quiet mode off" / "what mode am I in" by voice, the "5 conversations during
  school time means I'm home" rule, and the `[school]` settings. Minty no longer keeps itself quiet because it is a school day;
  an old `[school]` section in `config.toml` is simply ignored.
- What stays the same: reminders you set, the night window (`proactive.night_from` / `night_to`), the hourly cap on things Minty
  says by itself, and the holding of its own nudges when the policy says so (they are shown silently and said later as one digest,
  now introduced with "Earlier:" instead of "After school:").
- **Scheduler and proactive policy** no longer take a `quiet` function (`Scheduler(..., gate=...)`, `ProactivePolicy(cfg)`); the
  gate alone decides say, hold or drop.
- **Study planner** plans weekdays in `study.window_start/end` and weekends in `study.weekend_start/end` (it used to move the start
  after school ended); the busy slots and the night window still cut time out.
- **SchoolPad plugin** keeps its homework list and notices, but no longer reads the school calendar or holidays (the portal prompt no
  longer asks for HOLIDAY lines), and the automatic check "at the end of school" is gone: say "check school" when you want one.
- The morning brief no longer says "School day: ..."; a reminder's confirmation no longer mentions school time.
- Date helpers (`find_date`, `parse_hhmm`) moved to `datewords.py`.
- Tests: `test_daymodel.py` removed; `test_scheduler.py`, `test_proactive.py`, `test_school.py`, `test_plugin_reminders.py` and the study
  planner tests rewritten for the smaller design. `test_statusline.py` no longer depends on the date it was written on.

## 1.0.0-alpha.20 (2026-10-20)

### Two buttons: Mute and Listen
- **M Mute** and **Space Listen** on the top edge of the chat bar. Press the key or click the button (mouse reporting is switched on
  while the display is open and restored on exit).
- **Mute** turns the microphone off and on; while muted the whole display fades to red (the orb, borders, clock and status strip), the
  button says *Muted* and the bar says how to unmute.
- **Listen** starts a turn without the name (the next audio frame, about 80 ms later); saying the name still works. While Minty is
  speaking or working on an answer it cuts in, like an interruption. It does nothing while muted or already listening.
- The keys act only when nothing is typed, so Enter opens an empty line first when a message must start with `M` or a space (Enter or
  Backspace on the empty line closes it). Any other letter still starts typing at once.
- **Settings:** `terminal_mute_key`, `terminal_listen_key` (one character, or `space`; they must differ), `terminal_hotkeys` and
  `terminal_mouse` (off lets the mouse select text). The panel has the two keys under Voice, audio, safety & updates and the two
  switches under Switches. The panel dashboard gets a Listen button next to Mute, and the API gets `POST /listen`.
- Tests: `tests/test_hotkeys.py` (28), `tests/test_listen_now.py` (6).

### Updates from GitHub (prepared for publishing)
- **`minty update`** (`--check` only looks, `--yes` skips the question): finds the newest published release (pre-releases count, drafts
  do not), shows the notes, and after you say yes moves this git checkout to the release tag with a fast-forward only. It refuses to
  run over local changes, over commits of your own, from a different `origin`, or for a tag that is not there. It installs changed
  dependencies, checks that the new version starts, and rolls back to the commit that worked if it does not (the last good commit is also
  recorded in `update-state/last-good.json`).
- **Daily check** (`update.check`, on): one request to the GitHub releases API, one notice on the display per new release, nothing
  installed and nothing spoken. Throttled by `update.check_every_hours` (24).
- **`update.auto_install`** (off) installs by itself; `update.verify_signed` requires a signed tag. The docs say plainly why it is off.
- **Which repository:** `update.repo` (`owner/name`), or by default the one the checkout's `origin` points at, so a clone of the
  published project needs no setting.
- `docs/releasing.md` and `scripts/release.py` check that a tag matches the version and the changelog and print the publish commands.
  Nothing publishes by itself.
- Fixed: `minty tune --lite`, `minty update --check` and a sentence after `minty instant` were rejected by the command parser.
- Tests: `tests/test_selfupdate.py` (60, including real git repositories for the update and the rollback).

### One name
- The project carries one name everywhere: the old-settings-folder hint and its tests are gone, the changelog tells the story under one
  name, the example names in tests are neutral, and the repository starts from a single fresh commit.

## 1.0.0-alpha.21 (2026-10-20)

### Muted looks muted, Listen listens
- **While muted** the state line on the display says only **MUTED**, in red, whatever else is happening: no "waiting for", "listening",
  "speaking" or "thinking", and no spinner. The rest of the screen already follows the red accent.
- **Listen** (the button or the key) goes straight to **LISTENING**: the display says so at once (not after the next audio frame), and the
  spoken "Yes, sir?" (or the chime) is skipped, because pressing a button needs no acknowledgement. Saying the name still gets it. Pressing
  Listen while muted shows the notice "Muted. Press M to unmute." instead of doing nothing silently.
- Tests: new cases in `tests/test_listen_now.py` and `tests/test_hotkeys.py`.

## 1.0.0-alpha.22 (2026-10-20)

### A finer clock
- The header clock is drawn three times finer: each digit is a 9 x 15 pixel grid (it was 3 x 5), still pixel art, with the outer corners
  stepped by a pixel so the shapes read as proper numbers (the old 1, 6 and 0 could look alike). Pixels are drawn with half blocks, so they
  stay square. The colon is two small squares that blink as before, and the shading from the song's colour at the top to a deeper tone at the
  bottom is kept.
- The header is three rows taller when the clock is shown (screens 112 columns and wider); narrower screens are unchanged.
- Tests: `tests/test_tui.py` checks the grid size, that all ten digits differ, that only block characters are used and that every row has the same width.

## 1.0.0-alpha.23 (2026-10-20)

### Music is not mistaken for you; the microphone adapts; no more "I wasn't closed properly"
- **Music while listening.** The moment Minty starts listening (you said the name, or pressed Listen) every other app that is playing is
  turned down to a tenth of its volume (`audio.listen_duck`, `audio.listen_duck_level`), and back up when the conversation ends. It works
  for any player, including browser tabs and videos that cannot be paused by command, and happens before the pause, so a song is not
  heard as your words. The volumes are written to a small file while they are down: if Minty is closed or killed in that time, the next start
  turns the sound back up (and stopping normally always does).
- **The microphone follows the room and the device.** The volume that counts as speech is no longer a fixed number (`audio.auto_sensitivity`,
  on): the room's background level is learned and the bar set a little above it, with a small minimum, so a quiet microphone in a quiet room hears
  a quiet voice and a hot one does not hear a fan. The learned level now falls quickly when the room gets quieter (the music was turned down) and
  rises slowly, instead of staying high for seconds. **`audio.mic_sensitivity`** (1 = normal, 0.25 to 8) scales the bar for a microphone that is deafer or
  hotter than most; the bar never drops below just above the room. Both are in the panel next to the old `speech_rms_threshold`, which is still
  the starting level and, with `auto_sensitivity = false`, a fixed minimum as before. The wake-word listener uses the same bar.
- **Removed the "I wasn't closed properly last time" question.** After a crash, a kill or closing the window the next start is simply a fresh
  one: the old conversation is set aside in `journal-archive/` (newest five kept), never asked about and never restored, and the start-up notice no
  longer mentions it.
- Tests: `tests/test_mic_sensitivity.py`, `tests/test_listen_duck.py` (including the kill-and-recover case), updated `tests/test_journal.py` and
  `tests/test_wp06_acceptance.py`.

## 1.0.0-alpha.24 (2026-10-20)

### Chrome opens DuckDuckGo, not Google
- When the browser tool is asked for Google's home page or a Google web search (`google.com`, `google.co.in`, `/search?q=...`, with images and
  news kept as such), it opens **DuckDuckGo** instead, and "open Chrome" with no address starts there. Google's other sites (maps, mail,
  docs, the store, sign-in) are left alone. `browser_search` defaults to DuckDuckGo and treats "google" as DuckDuckGo.
- Why DuckDuckGo: tried with the same headed Chrome the browser tool uses, two searches each: DuckDuckGo returned results both times, Brave
  answered `429 Too Many Requests` on the first request, and Google went to its captcha page ("unusual traffic") every time.
- `avoid_google = false` in the browser plugin's settings brings Google back.
- Tests: `tests/test_webpage.py`, `tests/test_plugin_browser.py`, `tests/test_browser_recovery.py`.

## 1.0.0-alpha.25 (2026-10-20)

### A 12-hour clock with AM and PM
- The header clock shows the time as 12-hour with AM or PM (10:47 PM, not 22:47), the marker sitting beside the bottom of the digits.
  A one-digit hour leaves its first digit blank, so the colon and minutes stay where they are when the hour changes. Midnight reads 12:00 AM.
- The same on the rest of the screen and in speech: the "coming up" list (`11:59 PM`, `tmrw 9:30 AM`, `Wed 9:30 AM`), the battery forecast
  ("runs out around 3:10 PM"), calendar answers ("Next: ... at 3:00 PM", free time "8:45 AM to 3:00 PM"), the check-in time and the receipts list.
  One helper, `minty_core/timefmt.py`, does it.
- Tests: `tests/test_timefmt.py`, `tests/test_tui.py`, and the calendar tests.

## 1.0.0-alpha.26 (2026-10-20)

### Everything is a setting, and a new look for the control panel
- **New settings.** `clock_24h` (off) switches every time on the display and in speech between "3:10 PM" and "15:10". The browser plugin has
  `avoid_google` (on) in its settings, so the Google-to-DuckDuckGo redirect can be turned off from the Plugins page. The switches page has plain labels for the
  newer on/off settings (adaptive microphone, turning other audio down, speculative recognition, instant answers, terminal keys and mouse).
- **Settings page.** The old "Voice & Safety" page is now **Settings**: every setting that can be changed, in six groups (Voice, Microphone & music,
  Speed, Display & keys, Updates, Safety), each with a plain explanation, a filter box, a section index and one Save bar that says whether
  anything is unsaved. The list lives in `static/settings-schema.js`, and `tests/test_settings_registry.py` checks that every row is a real
  setting of the right kind and that all the recently added ones are there. The address `#audio` still works.
- **New look.** The panel is redesigned: deep navy, one cobalt accent, tall condensed capitals, squared panels with a corner mark, a blue frame, a halftone-dot header on the dashboard, a
  row-of-pixels microphone meter and switches instead of tick boxes. The whole panel turns red while the microphone is muted, as the terminal display does.
  **Ctrl+K / Cmd+K** opens a search bar that jumps to any page or straight to a setting (the row flashes). It works on a phone too.
  The display font is the condensed one that is installed on the computer (Nimbus Sans Narrow here); a closer match can be dropped in as `Antonio`.
- Tests: `tests/test_timefmt.py`, `tests/test_settings_registry.py`, `tests/test_panel_static.py` and `tests/panel_js_checks.mjs` (the settings page, filter and deep link).

## 1.0.0-alpha.27 (2026-10-20)

### A 500-question profile and Docker
- **Profile.** A new **Profile** page in the panel asks 500 fixed questions about you in 15 groups (about you, home, routine, health,
  fitness, food, sleep, school and work, interests, devices, how you like Minty, people, money, goals, limits). The numbers are permanent: a
  question is never reworded to mean something else, moved or removed. Answer one at a time (Interview) or from the list (All questions, with a filter), skip
  any, change or clear any answer, and download them all. Answers are kept on this computer in one private file (`profile/answers.json`, mode 600).
  **They are used:** with each request, the basics (about you, how you like Minty, your limits) and only those other answers that share words
  with the request are added to what the model is given, as short lines marked as background facts, within `profile.max_chars` (1800). `profile.enabled`
  switches that off (the answers are then only stored). New: `minty_core/profile.py`, `minty_core/profile_questions.py`, `GET/PUT/DELETE /profile`, a Profile group in Settings.
- **Docker.** `Dockerfile` (a small `core` image, and a `voice` image with the voice libraries), `docker-compose.yml` (the panel on `127.0.0.1:7778` only, data in a
  volume, not run as root, health check), `docker-compose.voice.yml` (passes the sound server and `/dev/snd` on Linux) and `docs/docker.md`, which also says plainly what
  does not work in a container (the Chrome browser tool, desktop control, a microphone on macOS and Windows). The files are checked by tests;
  the image could not be built in the environment where this was written, so the first build on a machine with Docker is the real test.
- Tests: `tests/test_profile_bank.py`, `test_profile_store.py`, `test_profile_prompt.py`, `test_profile_api.py`, `test_docker_files.py` and the Profile page in `tests/panel_js_checks.mjs`.

## 1.0.1 (2026-10-22)

### A tougher blank-slate default, and a release flow
- **Default persona.** The persona Minty ships with is now direct, honest, calm and unflattering, with no one's particular taste in it (no
  menace, no talk of loyalty): it answers first, says plainly when it cannot or should not, takes small safe steps on its own and checks before
  anything with consequences. Your own persona, edited in the panel, is a file in your settings folder and is never replaced.
- **Releases.** From the first release on, the version is a plain `1.0.1`, changed only when a release is made (`scripts/release_tools.py`: patch,
  minor, major, or `first` for 1.0.1); everyday changes are logged under `## Unreleased`. Pushing a tag such as `v1.0.2` starts the new release
  workflow (`.github/workflows/release.yml`), which makes the GitHub release that `minty update` looks for, using the changelog section as its notes.
  `scripts/leak_check.py` searches the tracked files for key-shaped strings, for the keys you actually use and for personal words (kept in files outside the repository).
- Tests: `tests/test_release_tools.py`, `tests/test_release_workflow.py`, `tests/test_version.py`, `tests/test_personality.py`.
- **Merging.** `CHANGELOG.md` merges line by line (`.gitattributes`), so a contributor's line and yours under `## Unreleased` no longer clash, and `release_tools.merge_unreleased` joins two Unreleased sections into one.
- **A PIN instead of a password.** The panel now logs in with a PIN of 4 to 8 digits, on a PIN pad (dots that fill as you type, a keypad for touch,
  the number keys, Backspace, Enter and Escape on a keyboard). `minty set-pin` sets it (`set-password` does the same); it refuses a PIN that is easy to
  guess (all the same digit, or digits in a row) and shows nothing as you type. The throttle is unchanged: five wrong tries a minute, then a wait.
  A longer password set earlier still works until you set a PIN (the login screen has "Use a password instead"). Use at least six digits if the
  panel is ever reachable from your network.
- **A control center, not a website.** The panel is now laid out like one: a menu on the left in three groups (Monitor, Configure, Memory) with small icons, a status bar
  along the top that is always there (what Minty is doing, a live microphone level, **Listen** and **Mute**, search and the time) and a page that scrolls on its own.
  The dashboard is a grid of tiles (microphone, system, brain providers, background jobs, talking to Minty, live transcript) instead of a big heading. While the
  microphone is muted the state reads MUTED and the whole panel turns red. On a phone the menu becomes a bar along the top that scrolls sideways.
- **Display font.** The tall, narrow capitals of the panel's headings now use **Antonio** (SIL Open Font License, 74 KB, in `static/fonts/` with its licence), so they look the same
  on every computer. The panel loads it from itself: nothing is fetched from a font service.
- **`minty set-pin --allow-easy`** accepts a PIN that is easy to guess (0000, 1234) when you ask for it, and says plainly that it is.
- **`minty profile-import FILE`** reads old free-form profile notes ("Label: answer" lines) and proposes which of the 500 questions each belongs to. It is a dry run;
  `--apply` writes only clear matches, never over an answer you already gave, and lists the rest to answer in the panel.
- **The "For you" panel is gone.** A fired reminder or the morning brief used to open a display pane that replaced the album card and the conversation; it is now a short
  notice (and is still spoken), so the album card stays as the default.
- **Tests that depend on the date are found automatically.** `MINTY_TEST_SHIFT_DAYS=60 pytest` runs the suite with the clock moved ahead (`tests/timeshift.py`); the two
  news tests that broke when the date changed were the only real cases.
- **Six speed-ups, all off until you switch them on** (Settings): `router.warm_on_wake` (open the connection to the provider when the name is heard), `turn.stable_prefix`
  (notes that change go after everything that does not, for providers that reuse the start of a prompt), `turn.lean_prompt` (the short wording of the fixed rules, without
  waiting for the comparison), `tts.parallel` (1 to 3 sentences made into speech at once, order kept), `stt.auto_model` (the local recogniser sized to the computer) and
  `audio.live_barge_in` / `live_barge_in_rms` (a voice clearly louder than the echo can interrupt the live voice). None of them can be judged without listening: try one at a time.
- **Code review fixes.** Found by reading the code, a check that every name a function uses exists (`tests/test_no_undefined_names.py`), fuzzing everything parsed from outside
  (`tests/test_fuzz_inputs.py`) and timing hostile input:
  - the spoken health command used a name that did not exist and would have crashed;
  - a damaged lockdown file switched lockdown **off** (now it stays on until you release it); a damaged `secrets.toml` no longer crashes every read and is never overwritten;
  - stopping a helper task (the sleep timer, the barge-in watcher, MCP, coding agents, the installer) swallowed the cancellation of whoever asked; background tasks nobody awaited could be
    collected mid-run and their errors lost (`minty_core/tasks.py`: `spawn`, `cancel_and_wait`);
  - the paused song was not resumed if the turn was cancelled during "Yes, sir?"; a closed terminal made the key reader spin at full speed;
  - hiding personal data in a big page took seconds (the e-mail pattern was quadratic), a 20,000-character message took two seconds in the instant answers, and finding overlapping secrets was quadratic;
  - the panel sends a Content-Security-Policy, forbids framing, and caps the login body; the updater checks the started version exactly (`1.0.1`, not `1.0.12`);
  - `install.sh` no longer replaces a `minty` command that points somewhere else (`--relink` to do it on purpose);
  - messages told you to run `minty-core ...`, a command the installer does not link: they say `minty ...` now;
  - the wellbeing prompt module no longer mentions a teenager or one country's crisis line.

## 1.0.2 (2026-10-22)

- **Plainer wording.** The README, package description and getting-started guide describe Minty generally (Linux, the AI providers you choose) instead of naming distributions or free tiers.
- **Tests that fit any release.** The version smoke test and the title test no longer assume an `alpha` version; the fake Slack token in the secret-scanner test is built at run time so GitHub's push protection does not flag it.

## Unreleased

- **A loop guard in the conversation.** If the model repeats the same call with the same result, swaps between two calls with nothing changing, or fails four calls in a row, it is told once not to repeat it; a second trip ends the turn with an honest message instead of spending every round. The planner loop uses the same detector.
- **Tool calls are checked before they run.** Missing required arguments, wrong types and values outside a tool's allowed list come back to the model as an `Error:` it can fix in one more try; a number or true/false written as text is repaired. A malformed call never asks you to approve anything and never reaches the tool, and repeated bad calls count toward the loop guard.
- **A cut-off answer no longer forgets what it already did.** When an answer is interrupted (a provider error, you cancelling) after a message was sent or something was changed, the turn is still rolled back, but one short note stays in the conversation saying what had already been done, so the next question ("did that go out?") is answered truthfully. Reads and failed or declined actions leave nothing behind.
- **A false "I sent it" is corrected out loud.** Sentences are spoken as they stream, so a claim that something was sent, deleted or installed cannot be taken back; if no tool actually did it this turn, a short correction is spoken straight after and kept in the conversation. It compares the answer with what the tools really did (no model call), ignores questions, conditions, negations and talk about the past, and can be switched off in Settings ("Correct a claim that did not happen").
- **Several lookups at once.** When one answer needs more than one lookup in the same round (weather and a search, say), they now run at the same time instead of one after the other. Only an audited list of stateless lookups qualifies (`PARALLEL_SAFE_TOOLS`: weather, web search, time, memory recall, file list and read); a browser scroll or click, anything that changes something, and anything that asks for approval still goes one at a time, so you are never asked two questions at once. Answers are recorded in the order they were asked, and you can turn it off in Settings ("Run several lookups at once").
- **A guide to extra tools through MCP** (`docs/mcp-extras.md`). How to add api-anything as a local MCP server with the existing `minty mcp` commands, what to check first (account access and each site's terms), and why Composio (needs a remote transport Minty does not have yet), Agent-Reach and billion-context are not connected. Written from the projects' own pages; none of them has been run with Minty.
- **A command with a spelling slip still works.** "Turn the volumn down", "pasue the music" or "set a time for 5 minutes" are understood as the command they are one letter away from, instead of waiting seconds for a model. It is deliberately narrow: only words of four letters or more, one edit from exactly one command word of five letters or more, at most two words changed, and the whole repaired sentence must then match a command. Short words ("ship it" never skips a song), locking the screen, reminders and the "list timers" question are never produced by a repair, and exact commands are never changed. Switch it off in Settings ("Understand a command with a spelling slip").
- **A terminal, off by default.** Minty can run a command (`shell_run`) once you switch on `shell.enabled` (Settings, "Let Minty use the terminal"). Only a handful of read-only commands run at once (`pwd`, `ls`, `whoami`, `date`, `uname`, `uptime`, `df`, `free`, `hostname`, `which`, `git status/log/diff/branch`), each checked word by word and run without a shell; any metacharacter sends a command back to "ask first". **Every other command is risk class 3: it is asked each time, with the exact command read out, whatever the safety settings say.** It never runs as another user (a `sudo`/`su` refusal is a convenience, not protection; the approval is the boundary), is never offered to background helpers, is stopped in lockdown, and in privacy mode only the read-only commands run. A command runs in its own process group with no keys in its environment, no input, a time limit (default 30 s) and a cap on output (first and last part kept, secrets masked); the whole group is killed on a timeout and when you speak over Minty. Its output comes back tagged as outside text. A command that exits with an error is reported as `Exit code N`, not as a tool failure, so three failing commands do not switch the terminal off; a spoken claim that something was done is accepted when a command ran. Background processes a command starts are stopped when it ends.
- **Carry on after a quick command** (off until you switch it on: Settings, "Carry on after a quick command"). Say "Minty, volume down", "mute" or "pause" over an answer and the command is done as before, but the rest of the answer, from the sentence that was cut off, is then said again by itself instead of being dropped. It applies only to quick, reversible, local commands (volume, mute, unmute, pause, resume, next, previous; never locking the screen, brightness, timers or reminders), only within 45 seconds of the interruption, and only once. It still needs the wake word: Minty cannot safely listen for a command while its own voice is playing unless echo cancellation is on, so it does not try.
- **Older conversation is summarised instead of forgotten** (off until you switch it on: Settings, "Summarise older conversation"). The voice window keeps only the newest turns; turns that fall out of it are now written up in a few sentences by a model, in the background, every few turns (`turn.fold_every_turns`, default 3), and the summary rides along as notes next to the one-line notes, so the start of each request stays steady for provider caches. The idea comes from context-folding tools such as billion-context, built into Minty's own history handling (the tool itself is not used). Only what was said and answered goes to the summariser, never tool output; keys are masked; a turn that touched private data is summarised only through the private route; it never holds up an answer; a failed summary changes nothing; the summary is kept in memory for the conversation only and starting fresh forgets it. Not measured: whether it makes answers better in practice; try it and judge.
- **The Docker image was built and run for the first time, and two start-up problems were fixed.** The core image has no microphone libraries on purpose, but Minty still tried to start the voice loop and crashed it six times with tracebacks; it now starts without voice and says so in one line (`voice is off: sounddevice is not installed`), and the panel and the control API start at once. A missing optional speech library is also one warning line instead of a traceback. Checked on the built image: it runs as the unprivileged user, writes `panel.toml` on the first start, publishes the panel only on 127.0.0.1, answers the panel and reports healthy. Not checked: the `voice` image, and use with a real key.
- **The terminal is tested through the real application** (`tests/test_shell_wiring.py`): with the real safety layer, approval guard and plugin host it is refused when switched off, a plain read runs without a question, `sudo` is refused before anyone is asked, any other command is asked with its exact text and a "no" runs nothing, "never ask", a trusted level and an "allow" rule cannot silence the question, a "deny" rule and lockdown stop it.
- **The terminal is on by default, and web search and opening results go through it.** `shell.enabled` now starts on (you asked for it; switch it off in Settings). What did not change: only a short read-only list runs unasked and **every other command is asked each time with the exact command read out**, whatever the safety level; never as another user; never for background helpers; stopped in lockdown. New on the unasked list, when `ddgr` is installed: `ddgr --json -n 5 words` (a web search, class 0 like Minty's other search) and `ddgr -j words` (opens the top result in your browser, class 1: asked in the careful level only). Other `ddgr` options and `xdg-open URL` are asked about, and privacy mode refuses both searches because they contact DuckDuckGo. A command that opens a window now gets where the screen and the browser are (never a key or a token). Minty is also told, only when a request could use one, which command-line tools this computer has (`ddgr`, `gh`, `git`, `jq`, `rg`, `instagram-cli`) and to prefer them; tools that use your login or your messages (`instagram-cli`) are always asked about and you log in yourself. Trade-off to know about: with the terminal on for everyone, a mistaken "yes" is the only thing between a bad command and your computer, so listen to the command that is read out.
- **Automations, set up and explained.** The n8n plugin is now on by default (without an n8n it only says how to set one up, and the message for a server that is not running now says how to start it). `docker-compose.n8n.yml` runs the official n8n on 127.0.0.1 only, with its usage reports and update checks off and no keys in the file. `docs/automations.md` explains the three ways (Minty's reminders, timers and routines; n8n workflows, created inactive and only run after you approve activating them; scripts and schedules through the terminal, which last until removed and are always shown first). When a request is about automating something, Minty is told to prefer n8n or its own reminders, and, with the terminal on, to show you the exact cron or systemd files and commands and how to undo them. Not done: n8n has not been started against this release, so the first workflow Minty builds should be looked over before you activate it.
- Coding-agent jobs are checked, not trusted: the folder is compared before and after, the project's own tests are run (found from its files, or set in `check_command`), failing tests send the agent back to fix them (`max_rounds`), and the verdict (verified, not confirmed, tests failing, nothing changed) is what Minty reports. Jobs survive a restart as "interrupted", `mode: ask` runs Claude Code read-only, and Claude is denied `git push`/`reset`/`rebase`. Progress while it runs is read from the disk.
- Missions (`missions_start`, on by default): hand Minty a goal and it plans, works in the background with your tools and checks each step against a ledger of what the tools really returned, not its own "done" (talking without acting cannot pass). It ends done (and says whether it was double-checked), failed with the reason, or blocked on one question; `missions_answer` and `missions_resume` carry on from the saved plan after an answer, a failure or a restart, and `missions_report` shows the plan, every action and the result. Risky steps wait for your yes through the existing approvals (panel, voice, Telegram); the terminal, installer and other helpers are never available to a mission.
- Coding agents are on by default (every start still asks first). `start_task` takes `wait: true` so a mission blocks until the files and tests have been checked and gets that verdict, and a coding agent that is not installed now says so plainly.
- E-mail you can hand over, under rules only you set (the `outbox` plugin, off until you switch it on and give it an address and an app password): `outbox_draft` writes, `outbox_send` sends. A reply to someone on your `auto_reply_to` list, answering a message that passed the sender checks (SPF/DKIM/DMARC), short, with no links, other addresses, secrets or sensitive words, under a daily cap, goes without asking and says Minty sent it; everything else, including every new conversation, asks with the whole message shown and is always asked whatever the safety mode. A reply can only go to the person who wrote. Every message is recorded (drafted, sent and how, failed, or unknown if the connection dropped), never sent twice, and `follow_up_days` adds a follow-up to your waiting list. No tool can change the rules.
- Minty now notices what needs you without being asked (`attention_review`, on by default, `proactive.attention`): a mission waiting for your answer, one that was interrupted or failed, an e-mail that may not have gone out, a draft left waiting a day, an overdue item, a follow-up that has come due. Each is said at most three times with a cooldown, never more than five a day in all, through the scheduler's gate (night hours and the hourly limit still apply), and `attention_dismiss` / `attention_snooze` silence one. When it resolves it disappears.
- The brain-dump sorter understands a real ramble: filler ("ok so", "also") is dropped, thoughts joined by ", and" are split, "waiting on mum to ..." is a follow-up, "the reel needs a better ending" is project work, "maybe" / "could" / "I think ... could" are ideas, "should I ...?" is a decision to make, and "I'll" / "I promised" are commitments. Applying skips what is already on your list (same words, any order), keeps decisions as "Decide: ...", and files the original ramble once as a note so the context is never lost.
- `missions_review` reads the record of missions and coding jobs and says, with the numbers, what could work better (a tool that keeps failing, a project whose tests fail the first time, goals that run out of steps, jobs nobody could check). A finding is only confirmed when it repeats across separate jobs, and it is a proposal: nothing is changed by itself. New guide [docs/work.md](docs/work.md), and ten evaluation scenarios (`tests/agent_eval/work_eval.py`) with failures injected on purpose and a stored baseline.
- Hardening from an independent review of the new work features: a risky mission step now waits on the one question the safety gate already put to you (it used to open a second one and wait on that, so approving the first left it stuck); helpers can no longer start missions or send mail, and any tool whose risk depends on its arguments counts as asked; a coding job's tests are run before the agent starts so old failures are not blamed on it; an e-mail sender is only trusted when SPF/DKIM passed for the sender's own domain; every mission action is saved as it happens and a resumed step is told what it already did; a declined step is recorded as not done; the attention check runs on the event loop.
- Missions are given the facts you told Minty that bear on their goal (your own words only, never inferred or private ones, each with where it came from), so they stop asking for things Minty already knows. Three built-in workflows (handle my email, delegate a coding job, hand over a goal) teach the right sequence of tools. `missions_review` now also offers, never grants, letting someone be answered without asking once you have said yes to five replies to them with nothing going wrong. `docs/work.md` explains the five stages (observe, prepare, execute, commit, escalate) and who decides each.
- Recipes (`recipes_create`, on by default, none exist until you ask for one): set up an automation by talking, for example "every day at 18:00 make a reel and queue it for my approval". Each run is a mission with all its checks and approvals; it runs once a day, never two at once, a run missed while the computer was off is skipped (not made up at 3 am), and three failures in a row pause it with the reason and put it on your attention list. Creating one is always asked, with the whole plan shown; a recipe holds no permissions of its own. `recipes_list`, `recipes_run_now`, `recipes_manage` (pause, resume, delete).
- The studio (`studio_make`, `studio_draft`, `studio_send`, `studio_posts`, on by default; posting needs your own setup and asks first): a voiceover (the local Kokoro voice, offline), a plain generated music bed, captions burned in, and a background clip (your own gameplay footage, or a free Pexels clip with its credit added) become a 9:16 video, checked by what is really in the file (shape, codecs, audible sound, not black, length). Files are kept only with a licence on record, and a post made from anything else is never automatic; nothing is ripped from video sites. Posts go through Meta's official Graph API (reels by resumable upload; carousels from a public folder you serve), are queued, recorded and never sent twice, are asked before every post unless you set `studio.post_mode = auto` yourself (then only clean posts, one a day, six hours apart), are labelled AI-made unless you say otherwise, and an unclear result is "unknown", not posted. The Instagram token is only ever sent to Meta's hosts, and a warning appears a week before it runs out. Not tested against a real account.
- Claude Code as the motion-graphics maker: `studio_make project` sets up a folder with a brief and a check script, a project can name its own check in `.minty/check`, so a coding job counts as done when what it made passes (not when it says so), and the agents setting `claude_extra_tools` adds tools such as `Bash(npx:*)` (never a blanket allow, rm, sudo, curl or wget). ffmpeg and ffprobe are now in Claude's default tools. New skills: make a reel, set up an automation. New guide `docs/studio.md`; two more evaluation scenarios (a recipe runs once a day and stops itself; posting follows your rules).
- Studio follow-ups: a real Claude Code job was run in a studio folder and Minty's verdict was verified from its output; `studio_make refresh_token` renews the 60-day Instagram token (asked, never shown) and the expiry warning follows it; `studio_posts insights` reads how posts did, and with tags on drafts `missions_review` says which kind of post reaches at least 1.5x as many people (three or more posts each side); the studio folder gets a `BRAND.md` for Claude Code to follow; a recipe is tested end to end through the real app (it makes a draft and posts nothing).
- Hardening of the studio and recipes from an independent review: a coding agent can no longer grade itself (the check a job started with is the one that runs after, and a job that edits `.minty/check` or the script the check runs is reported as failed); the Pexels credit has no link, so stock footage no longer forces every post to ask; `studio_draft` takes what Claude Code made in the studio project's `out/` folder and treats it as yours, so the unattended path needs no manual step; a mission waits six hours (`missions.approval_wait_s`) for your yes to a risky step instead of five minutes, and an unanswered question is recorded as "no answer came in time", not as a no; no recipe starts during a lockdown; the draft video can be sent to your paired Telegram chat (`studio.preview_to_phone`, default off); the Instagram API version is a setting and defaults to v26.0, the one Meta's examples use.
- Security: the control API's WebSocket takes its token in an `Authorization` header and refuses one in the address. uvicorn logged
  `WebSocket /events?token=...` to the console, journal and `minty.log`; a filter now hides secret-looking query values on its loggers
  too, and `minty.log` is private (0600, also after a rollover). A closed events socket is now noticed at once instead of at the next
  event, so it no longer keeps its place on the event bus or holds up shutdown. `websockets>=14`. Tests: `tests/test_security_ws_logs.py`.
