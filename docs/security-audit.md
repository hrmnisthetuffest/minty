# Security, speed and video audit (2026-10-28)

What was looked at, what was found, what was changed, what was measured, and what is still open. Everything under "found" was
reproduced or read in the code before it was changed; everything under "fixed" has a test that failed first. Nothing here is a
guarantee that Minty is secure: it is a list of specific weaknesses that were closed, and of specific ones that were not.

## How it was done

- Read the paths where something untrusted meets something powerful: the web panel and the control API, the coding-agent jobs, the
  studio's media handling, logging, downloads, the Docker and systemd files, the dependency list.
- Static analysis: `bandit` on `src/` (medium and above), and `pip-audit` on a freeze of the `personal` environment (89 packages,
  `--no-deps`). Both are repeatable with `python scripts/security_scan.py`.
- Ran for real: a Claude Code job inside the new sandbox (twice, `verified`), the NASA retrieval against NASA's live library, a Pexels file
  download, the panel in a browser with its network panel measured and a real Chrome playing clips, the core and voice container images with the
  hardened settings, ffprobe/ffmpeg against hand-made playlist files, and the studio tools end to end on live NASA footage (discover, pick a
  background automatically, offline voiceover, captions, check, credit added, queued and not posted: a 7.1 s 1080x1920 reel of 2.1 MB).
- Did not run: Instagram, the Pexels and Pixabay search APIs (no keys), SMTP/IMAP, Telegram, the app as a system service (the systemd options were tried one by one on a transient unit), a fuzzer, a penetration test.

## Found and fixed

| # | Severity | What | Fix | Test |
|---|---|---|---|---|
| 1 | High if a job reads hostile text | A Claude Code job inherited Minty's whole environment (provider keys, bot tokens, the SSH agent), could run python/pip/npm, and had no limit on which files it read. Missions and recipes start such jobs unattended. | Environment allowlist (`agents.agent_env` adds names); the agent and the project tests it wrote run in bubblewrap (own project folder, own sign-in folder, network for the agent, none for the tests; `agents.sandbox` auto/required/off); deny rules for the Read/Edit tools on key folders. The result says whether it ran sandboxed. | `test_security_agents_env.py`, `test_security_agents_isolation.py` (real bubblewrap: keys hidden, project writable, no network for tests) |
| 2 | Medium | The control API's WebSocket token travelled in the URL; uvicorn logs `WebSocket /events?token=...` (INFO) to the console, the systemd journal and `minty.log`, which was world-readable (0644). | Token in an `Authorization` header only (a token in the address is refused even when right); a filter hides secret-looking query values on uvicorn's loggers; `minty.log` is 0600 from the first byte and after a rollover. | `test_security_ws_logs.py` (a real uvicorn server, log lines captured; mutation-checked) |
| 3 | Medium (LAN attacker) | PIN guessing: a flat 5 tries a minute per address meant a 4-digit PIN fell in about a day and a half, with no log of the attempts. | Each lockout of an address is twice the last (1 min to 1 h, forgiven after a day without failures), a ceiling of 30 PIN checks a minute for the whole panel, `Retry-After`, and `minty.security` log lines (address and reason only) for refused logins, lockouts, bad hosts, requests from outside the network, missing CSRF headers, refused sockets. Read them with `/logs?name=minty.security`. | `test_security_panel_login.py` |
| 4 | Low-medium | A closed events socket was only noticed at the next event: it kept its place on the event bus and held up shutdown while the panel was open. | The handler also watches for the disconnect. | `test_security_ws_logs.py` |
| 5 | Low-medium | The 340 MB of voice models were downloaded and loaded with no integrity check. | Pinned SHA-256 per file (Piper: Hugging Face's published digests; Kokoro: pinned from the copies downloaded on 2026-10-10, GitHub publishes none for that release); a non-matching download or existing file is replaced. | `test_security_download_integrity.py` |
| 6 | Low-medium | A file's name or a server's word decided what the studio thought it was; `probe` and the library took anything ffprobe could open, and `studio_make add` copied any file in the home folder into the library (from where a post could upload it). | Decided from the first bytes (a closed list of binary media formats); a playlist, concat list or document is refused; the library stores files under a name made from their bytes (never `.m3u8`); every ffmpeg/ffprobe input is `-protocol_whitelist file`. ffmpeg 9 with default settings did not read local files or addresses from a crafted playlist (tested), so this is defence in depth. | `test_security_media_sniff.py` |
| 7 | Low-medium | A Pexels creator's name (written by a stranger) went straight into the caption of a public post; titles and tags went into tool results the model reads. | `studio/safetext.py` cleans names, titles, credits and notes at ingestion: one plain line, no addresses, `@`, `#`, markup, bidi characters or orders to a model. | `test_security_studio_text.py` |
| 8 | Latent bug | The sandbox set `HOME` before `--clearenv`, which wiped it. | Set after. | `test_security_agents_isolation.py` |
| 9 | Low | Clips removed to keep the library small (`library_max_mb`) could still be offered by `pick`, by id and in the library listing. | Removed files are skipped and marked "(file removed)"; the record and its licence stay. | `test_studio_assets.py`, `test_plugin_studio_catalog.py` |
| 10 | Low | `certifi 2026.7.22` and `filelock 4.1.0` in the `personal` environment have advisories (PYSEC-2026-4320; six for filelock). That environment is yours and was not changed. | Floors in `pyproject.toml` (`certifi>=2026.10.18`, `filelock>=4.2.1` in the voice extra); upgrading is `pip install -U certifi filelock` in that environment. | `scripts/security_scan.py` |

Accepted after reading each site (the numbers are in `scripts/security_baseline.json`, and the scan fails on any new finding): 45 SQL
strings built from constant column lists or allowlisted names, never from a request (B608); 7 SHA-1 uses for ids and de-duplication
(B324); 5 `/tmp` mentions that are the sandbox's own tmpfs or text (B108); 4 ElementTree parses of RSS/arXiv answers already capped at
1 MB, where the bundled expat refuses entity bombs and ElementTree never fetches an entity (B314).

Already in place and re-checked, not changed: SSRF guard with DNS pinning and per-hop redirect checks, CSRF header and `SameSite=Strict`
cookies, Host and Origin checks, a strict CSP, `lan_only`, scrypt-hashed PIN, Telegram pairing (one-time code, 5 tries, 10 minutes),
secrets files at 0600 in a 0700 folder, injection tagging and the approval classes.

## Measured

| What | Before | After |
|---|---|---|
| Panel reload (23 requests, in a browser) | 189 418 bytes, 0 answered by 304 | 6 600 bytes, 22 answered by 304 |
| Panel first load, no cache (woff2 font, gzip for its own text files) | 189 418 bytes | 83 441 bytes (-56 %) |
| Tool menu sent with every request (`need_toolkit`) | 3 258 characters | 2 287 |
| A plain question: system prompt + tools (`scripts/perf.py` tokens) | 2 979 | 2 736 (-8.2 %) |
| A real NASA background clip | 55.0 MB downloaded | 4.48 MB kept (60 s, 720x1280, no sound), 10 s to fetch and prepare |
| Import time / memory after import | 364 ms / 172 MB | 359 ms / 172 MB (no change) |
| The running app (core image, hardened container, no voice): panel answering after start / idle CPU / memory | not measured before | 1.1 s / 0.53 % of one core over 60 s / 83 MB |
| A real 34 s Pexels clip, kept as a background | 29.5 MB (bigger than the 25.0 MB download) | 10.9 MB (capped at about 2.5 Mbit/s) |
| Full test suite | 5 566 collected, green | 5 700+ |

The size budgets in `scripts/perf_budget.toml` had been exceeded since before the agentic overhaul (a plain question was 2 597 tokens
against a limit of 1 500) and nothing enforced them. They are re-baselined just above the measured values and
`tests/test_request_size_gate.py` fails when a change makes a request bigger. About 25 background loops wake every 30 to 600 seconds;
that was read from the code; the running app was measured in a container without a microphone (see the table).

## Video

There is no panel background video in Minty; "background video" is the footage behind a reel in the studio. See
[studio.md](studio.md#background-styles-a-different-look-on-different-days) for the catalogue, the daily rotation and the providers' terms.
Verified: real retrieval from NASA's library; rotation, repetition, thin and empty catalogues, exclusions, favourites, restarts, expiry,
timeouts, rate limits, hostile addresses and bytes, size limits (with scripted providers). Verified for real: a Pexels file link (they need no key) downloads straight from `videos.pexels.com` with no redirect, through the guarded fetcher.
Not verified: the Pexels and Pixabay search APIs (no keys), Pixabay's download host, and whether a caption credit without a link satisfies Pexels' API attribution wording.

### Playback in the panel

The studio page (`#studio`) plays today's clip. One clip plays and at most one more is loaded ahead in a hidden element, so a switch is a fade
and not a flash; a clip that cannot be played says why and can be skipped; nothing loads by itself on a data-saving or slow connection or
with reduced motion (a picture and a Play button); everything is released when the tab is hidden or the page is left. Clips are streamed by
the panel from the core by number, for a logged-in session only, with byte ranges, kept a day in a private cache; the core serves only clips
that are in the catalogue and inside the library folder, and poster frames (about 15 KB each) are made once. Measured against a real core on
localhost, in a real Chrome (`tests/test_panel_studio_browser.py` repeats it): frames advance, first frame 19-36 ms, a switch to the clip loaded
ahead 0 ms with the old element released, the dimmed backdrop plays, and leaving the page leaves no video element. Not observed: anything over a
real network or on a phone.

## Still open

- A sandboxed Claude Code job still has the network (it needs it to reach its model) and its own sign-in folder, so something it was
  talked into running could send its Claude login token out. A network allowlist (a proxy) was not built.
- Hermes and OpenClaw jobs are not sandboxed (their needs are unknown); they do get the cleaned environment, so a key they need must be named
  in `agents.agent_env`.
- The panel speaks plain HTTP on your network by design; put it behind HTTPS if you open it beyond your home.
- The systemd unit has only the options that were tried with Minty's own sandbox, audio and ffmpeg and left them working (`LockPersonality`,
  `SystemCallArchitectures=native`, `ProtectKernelModules`, `ProtectControlGroups`). `RestrictSUIDSGID` and `ProtectKernelLogs` each stop
  bubblewrap from starting (found by trying them), and `NoNewPrivileges` would stop a `sudo` command you approve, so they are not used.
- The Docker Compose file drops every capability, forbids gaining privileges and makes the program read-only; run on the core image and on the
  voice image (capabilities empty, `NoNewPrivs` on, `/data` writable, panel answering, the voice libraries import). A microphone and speaker
  passed through to the container were not tried.
- The Kokoro digests trust the copy downloaded on 2026-10-10.
- NASA items with third-party content are filtered by words in their description (a heuristic); the Pixabay and Pexels clips are the
  providers' word for their licence.
- `requirements.lock` covers the core dependencies for Python 3.12 only (not the optional extras); the default install is still unpinned.
- `bandit` and `pip-audit` only see what they are given: one environment, no optional extras that are not installed.
