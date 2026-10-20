#!/bin/sh
# Starts Minty inside the container. On the first start only, the panel is told to listen on every address of the container, so the
# port the compose file publishes (on 127.0.0.1 of your computer only) can reach it. An existing panel.toml is never touched.
set -e
config="${MINTY_CONFIG_DIR:-$HOME/.config/minty}"
mkdir -p "$config"
if [ ! -e "$config/panel.toml" ]; then
  printf 'host = "0.0.0.0"\n' > "$config/panel.toml"
fi
exec "$@"
