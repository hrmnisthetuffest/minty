# Running Minty in Docker

Docker gives Minty a clean, repeatable setup on any machine that runs it: no Python, no virtual environment, nothing installed
on the host. It is the easiest way to **try Minty** or to keep it running on a **server or a spare computer**. For daily use at your
own desk with a microphone, the normal install (`./install.sh`) is still the better fit: see "What does not work" below.

## Quick start

You need Docker with the compose plugin (`docker compose version`).

```bash
git clone https://github.com/hrmnisthetuffest/minty.git
cd minty
docker compose up -d --build
docker compose run --rm minty minty set-pin           # the panel's login PIN
docker compose run --rm minty minty set-key groq      # a free model key (asks without showing it)
```

Open **http://127.0.0.1:7778**, log in, and open Settings. The panel answers on this computer only: the compose file publishes it on
`127.0.0.1:7778`, never on your network. Keep the host port at 7778 (the panel only accepts that address).

Everything Minty keeps (settings, keys, memory, your 500-question profile) is in one Docker volume, `minty-data`, so removing and
rebuilding the container loses nothing. Back it up or inspect it with `docker run --rm -v minty-data:/data alpine ls -la /data`.

## Everyday commands

| What | Command |
|---|---|
| Start, stop | `docker compose up -d` · `docker compose down` |
| Logs | `docker compose logs -f minty` |
| The terminal display (stop the background copy first: two copies must not share the data) | `docker compose stop minty && docker compose run --rm minty minty` |
| Any Minty command | `docker compose run --rm minty minty instant "what is 12 times 8"` |
| Update | `git pull && docker compose up -d --build` |
| Health | `docker compose ps` (the container reports healthy once the panel answers) |
| Remove everything, data included | `docker compose down -v` |

## Voice (Linux with PipeWire or PulseAudio)

```bash
docker compose -f docker-compose.yml -f docker-compose.voice.yml up -d --build
```

This builds the `voice` image (the voice libraries, ffmpeg, the sound tools) and passes your sound server and `/dev/snd` through, so
the container uses your microphone and speaker. If your user id is not 1000 (`id -u`), set `MINTY_UID` and `MINTY_GID` first. The
speech and voice models are downloaded into the volume the first time you use them (`docker compose run --rm minty minty onboard --step voice`),
which can be large.

## What works

The panel (Settings, Profile, Brain, Plugins, Memory and the rest), the control API, text chat, Telegram, the instant answers, timers
and reminders, notes, the tools that need only the internet, and with the voice image the voice itself.

## What does not work

Docker keeps programs away from the rest of your computer, which is the point, and also the limit:

- **The Chrome browser tool.** It drives a real Chrome window on your screen, which a container does not have.
- **A microphone on macOS or Windows.** Docker Desktop there cannot pass the sound hardware through; run Minty normally for voice.
- **Controlling your desktop:** pausing the music player, the global push-to-talk key and the on-screen HUD act on the machine they run on, which here is the container.
- **`minty update`.** An image is updated by rebuilding it (see the table above), not from inside.

## Safety

The image runs as an ordinary user, not root; it holds no keys (they are added to the volume afterwards with `set-key`); the control
API is not published; and `.dockerignore` keeps `.git`, key files and your own notes out of the build. The compose file also drops every
Linux capability, forbids gaining privileges, and makes the container's program read-only (only the `/data` volume and `/tmp` are writable).
Checked by running the core image that way: all capabilities empty, `NoNewPrivs` on, the program read-only, `/data` writable and the panel
answering. The voice variant was run the same way (the panel answered and its voice libraries import); a microphone and speaker passed through to it
were not tried: if sound misbehaves, remove the `read_only` and `tmpfs` lines first.
