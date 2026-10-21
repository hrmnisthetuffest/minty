#!/usr/bin/env bash
# Installs Minty as a systemd user service that restarts on failure and starts at boot (with lingering).
# The unit text comes from `minty service-unit`, so it is tested and has one source.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
unit_dir="$HOME/.config/systemd/user"
mkdir -p "$unit_dir"
PYTHONPATH="$root/src" "$root/.venv/bin/python" -c "import sys; from minty_core.app import main; main(['service-unit', '--root', sys.argv[1]])" "$root" \
  > "$unit_dir/minty-core.service"
systemctl --user daemon-reload
systemctl --user enable --now minty-core.service
echo "Installed. Logs: journalctl --user -u minty-core -f"
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
  echo "To start at boot without logging in, run once:  loginctl enable-linger $USER"
fi
echo "The panel runs inside the same process. Set its password first if you have not: \"$root/bin/minty\" set-password"
