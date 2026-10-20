#!/usr/bin/env bash
# Install Minty and start the guided setup.
#
#   ./install.sh                     from a checkout of this repository
#   curl -fsSL <raw url of this file> | bash      clones the repository first (needs git)
#
# It makes a private virtual environment in <checkout>/.venv, installs Minty into it, puts a `minty` command in
# ~/.local/bin and runs `minty onboard`. It never uses sudo and downloads nothing but the Python packages Minty needs;
# models, voices and optional extras are offered by the setup, one at a time, after asking.
#   --no-onboard   install only        --help   show this text
set -euo pipefail

REPO="${MINTY_REPO:-https://github.com/YOUR-USER/minty.git}"   # set MINTY_REPO, or edit this line after you publish
HOME_DIR="${MINTY_HOME:-$HOME/.minty}"
BIN_DIR="${MINTY_BIN:-$HOME/.local/bin}"
ONBOARD=1

for arg in "$@"; do
  case "$arg" in
    --no-onboard) ONBOARD=0 ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "install.sh: unknown option $arg" >&2; exit 2 ;;
  esac
done

if [ -t 1 ]; then ember=$'\033[38;5;208m'; red=$'\033[38;5;160m'; dim=$'\033[2m'; off=$'\033[0m'; else ember=; red=; dim=; off=; fi
say()  { printf '%s\n' "${ember}▸${off} $*"; }
fail() { printf '%s\n' "${red}✗ $*${off}" >&2; exit 1; }

printf '%s\n' "${red}Minty${off} ${dim}installer${off}"

# 1. Python 3.10 or newer
python=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    python="$candidate"; break
  fi
done
[ -n "$python" ] || fail "Python 3.10 or newer is needed. Debian/Ubuntu/Mint: sudo apt install python3 python3-venv · Arch: sudo pacman -S python · macOS: brew install python"
say "Using $($python --version) ($(command -v "$python"))"

# 2. Where the code is
here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$here" ] && [ -f "$here/pyproject.toml" ] && [ -d "$here/src/minty_core" ]; then
  root="$here"
else
  command -v git >/dev/null 2>&1 || fail "git is needed to download Minty (sudo apt install git)"
  case "$REPO" in *YOUR-USER*) fail "Set MINTY_REPO to the repository address, for example: MINTY_REPO=https://github.com/you/minty.git" ;; esac
  root="$HOME_DIR/src"
  if [ -d "$root/.git" ]; then say "Updating $root"; git -C "$root" pull --ff-only
  else say "Downloading Minty into $root"; mkdir -p "$HOME_DIR"; git clone --depth 1 "$REPO" "$root"; fi
fi

# 3. A private environment
if [ ! -x "$root/.venv/bin/python" ]; then
  say "Creating the virtual environment"
  "$python" -m venv "$root/.venv" || fail "Could not create a virtual environment. On Debian/Ubuntu/Mint: sudo apt install python3-venv"
fi
say "Installing Minty (this takes a minute)"
"$root/.venv/bin/python" -m pip install --quiet --upgrade pip
"$root/.venv/bin/python" -m pip install --quiet -e "$root"

# 4. The `minty` command
mkdir -p "$BIN_DIR"
[ "$BIN_DIR/minty" -ef "$root/bin/minty" ] || ln -sf "$root/bin/minty" "$BIN_DIR/minty"
say "Linked $BIN_DIR/minty"
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) say "Add this to your shell profile so \`minty\` is found:  export PATH=\"$BIN_DIR:\$PATH\"" ;; esac

# 5. The guided setup
if [ "$ONBOARD" = 1 ]; then
  say "Starting the guided setup"
  exec "$root/bin/minty" onboard
fi
say "Done. Run:  minty onboard"
