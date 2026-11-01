# Minty on Windows

The same repository installs on Windows 10 and 11 (64-bit) and on Linux. Windows has its own installer (`install.ps1`, with `install.cmd` to double-click); everything else is the same code.

> **How much to trust this.** The Windows support was written and checked on Linux: the code that differs between the two systems is in one module (`minty_core/osutil.py`), tests fail if a Linux-only call
> appears anywhere else, the whole program imports and starts with every Linux-only module missing (which is what Windows is), every dependency was checked to be installable for 64-bit Windows,
> and the installer, launchers and checks were read for what Windows PowerShell 5.1 and `cmd.exe` accept. **It has not yet been run on a real Windows PC by its author.** The repository's CI is set up to run the
> installer and the install check on a clean Windows machine for every change (`.github/workflows/ci.yml`, job `windows`; it runs once the repository is on GitHub); until that has run green once, treat the first install as a test and run
> `scripts\windows_check.ps1` (below). If anything fails, the output it saves is exactly what is needed to fix it.

## Install

You do not need administrator rights, and nothing outside your own user account is changed.

**Easiest: double-click `install.cmd`** in the downloaded folder. It runs `install.ps1` for that one run only (the script policy on your PC is not changed), then starts the guided setup.

**From PowerShell**, in the folder:

```powershell
.\install.ps1
```

**Without downloading anything first** (needs the repository's public address; see "Sharing it" below):

```powershell
irm https://raw.githubusercontent.com/<you>/minty/main/install.ps1 | iex
```

What it does, and nothing more:

1. Finds Python 3.10 or newer (`py -3.12`, `python`...). If there is none, it **asks** before installing Python 3.12 with `winget` (for your user only).
2. Uses the folder you ran it from, or downloads Minty to `%LOCALAPPDATA%\Minty\src` (with `git` if you have it, otherwise as a zip file).
3. Makes a private virtual environment in `.venv` and installs Minty into it.
4. Puts a `minty` command in `%LOCALAPPDATA%\Minty\bin` and adds that folder to **your** PATH (not the system's). Open a new terminal to use it.
5. Starts the guided setup (`minty onboard`): the brain, voice, safety, Telegram. Skip with `-NoOnboard`.

Options: `-NoOnboard`, `-Relink` (replace a different `minty` command), `-Uninstall` (remove the command, the PATH entry and the start-at-login task; your settings and notes stay),
`-Yes` (agree to installing Python), `-Python <path>`, `-Repo <url>`, `-Branch <name>`. Settings by environment variable: `MINTY_HOME`, `MINTY_BIN`, `MINTY_REPO`, `MINTY_BRANCH`.

Then: `minty` starts it (say "Minty", or type), the panel is at http://127.0.0.1:7778 (`minty set-pin` first), and `minty onboard --step voice` re-runs one step.

### If it does not start

Run the check and send what it prints (it also saves a copy to `%TEMP%\minty-check.txt`):

```powershell
.\scripts\windows_check.ps1
```

It checks Python and the packages, that text keeps its accents, that your settings folder can be written, that a second Minty is refused, that stopping a program stops what it started, time zones, that the
second brain captures and finds a thought, and then **starts Minty for real** on free ports and asks the panel to answer. `-Quick` skips that last one. Voice, the microphone and optional programs are reported as WARN,
never FAIL: Minty works without them.

| You see | It means | Do this |
|---|---|---|
| "running scripts is disabled on this system" | PowerShell's script policy | double-click `install.cmd` instead, or run `powershell -ExecutionPolicy Bypass -File .\install.ps1` |
| "Python was not found" / the Microsoft Store opens | only the Store's placeholder `python.exe` exists | let the installer offer winget, or install Python from python.org and tick "Add python.exe to PATH" |
| "DLL load failed ... onnxruntime" | the Visual C++ runtime is missing (voice needs it) | `winget install Microsoft.VCRedist.2015+.x64` |
| a path or file name error during install | Windows' 260-character path limit | install from a short folder such as `C:\Minty` |
| the antivirus quarantines something | it is wary of a new program that listens to a microphone | allow the Minty folder; nothing in it is hidden |

## Where things are

| What | Where |
|---|---|
| Settings, keys, databases, your notes and second brain | `%LOCALAPPDATA%\Minty` (`MINTY_CONFIG_DIR` overrides it) |
| Voice models and other big downloads | `%LOCALAPPDATA%\Minty\data` |
| The code and its environment | `%LOCALAPPDATA%\Minty\src` (or the folder you installed from) |
| The `minty` command | `%LOCALAPPDATA%\Minty\bin` |

On Linux the same things are in `~/.config/minty` and `~/.local/share/minty`. **Privacy on Windows:** Linux makes the files readable by you only (mode 0600). Windows has no such mode; the folder inherits your user
profile's permissions, which already exclude other accounts on the PC (but not an administrator, and not anyone who can sign in as you). The same files, encrypted backups included, are protected the same way
by Windows' own permissions, not by anything Minty adds.

## What is expected to work

"Yes" below means: no Windows-specific blocker was found in the code, the dependencies install, and the Linux-side checks pass. It does **not** mean someone has used it on a Windows PC yet (see the box at the top).

| | Windows |
|---|---|
| Chat by typing, the panel, the terminal display, Telegram, every provider, Composio apps and mail | yes |
| The second brain (capture, the map, automatic filing), the world model, missions, workflows, research, notes, memory, reminders, timers | yes |
| Browsing (Playwright), files in your workspace, web search, finance, health, study and school tools | yes |
| Voice: wake word, speech recognition, voices, push-to-talk (a global key) | yes (needs the voice packages; the Visual C++ runtime; a microphone) |
| Start at login | yes: `minty autostart on` (a per-user scheduled task, 30 seconds after login, no window) |
| Encrypted backups, `minty update` (when installed with git) | yes |
| The terminal tool (`shell_*`), system help (`system_*`), the package installer (`installer_*`), Spotify control, song recognition | **no**: switched off, and never offered to the model (their safety rules, and the tools they call, are for Linux) |
| Echo cancellation for speakers, turning other programs down while Minty listens, pausing music while you talk | **no** (PipeWire / D-Bus). Use headphones, or say the wake word to interrupt |
| The sandbox that isolates coding agents | **no** (bubblewrap is Linux only). Anything that requires it is refused, never run unsandboxed. **Coding agents (Claude Code and the others) are therefore refused on Windows by default**: with no sandbox they could read your keys and files. If you want them anyway, set the agents plugin's `sandbox` setting to `off` (a choice, said out loud every time) |
| Scanning the home network, app scenes ("open app X"), the X11 overlay | no (web-address scenes work) |

Ask Minty "what can you do right now?" (`self_can`) or open the **Plugins** page: a plugin that cannot work on this system says "not available on this system" and why.

### Programs Minty starts

Minty starts Claude Code, MCP servers (`npx`, `uvx`) and similar programs with a trimmed environment, to which it adds what Windows needs for a program to start (`SystemRoot`, `USERPROFILE`, `APPDATA` ...). Programs installed with npm are `.cmd` files on Windows, and `cmd.exe` reads `&`, `|`, `%`, `^`, `!` and quotes in the arguments as commands: when the text of a request would reach one, Minty refuses and says so instead of letting a request become a command. Install the program's native `.exe` version (for Claude Code, its own Windows installer) to avoid it.

## Sharing it with friends

1. Publish the repository and put its address in `install.ps1` (the `$Repo` default, the same place `install.sh` has `REPO`), or have them set `MINTY_REPO`. Until then the installer refuses to download.
2. Send them one of: the link to the repository plus "download the zip, unzip, double-click `install.cmd`"; or the `irm ... | iex` line above.
3. They need their own free API key (the setup links to each) or a Claude Code login; nothing of yours is shared.
4. Ask them to run `scripts\windows_check.ps1` if anything looks wrong and send you `minty-check.txt`.

## For developers

- `src/minty_core/osutil.py` is the only place that knows the difference: process groups and killing a tree, resource limits, memory and handles, opening a file, the console, keys, restarting.
  `tests/test_portability.py` fails if a POSIX-only name is used anywhere else, if such a module is imported at the top of a module, or if a text file or text subprocess output does not name its encoding.
- A plugin that only works on some systems says so in `plugin.toml`: `platforms = ["linux"]`. It is then never loaded elsewhere.
- `python scripts/check_windows_deps.py` asks pip, from any system, whether every dependency group can be installed on 64-bit Windows (it installs nothing).
- `python scripts/install_check.py` is the check above on any system.
- Batch files are kept with Windows line ends (`.gitattributes`: `*.cmd -text`); PowerShell scripts are plain ASCII, so Windows PowerShell 5.1 reads them correctly.
