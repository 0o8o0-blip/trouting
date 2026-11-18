#!/bin/sh
# Link this checkout's plugin into KiCad 9 (for development: edits show up after
# Tools > External Plugins > Refresh Plugins). Users install the PCM zip instead.
set -e
here=$(cd "$(dirname "$0")/.." && pwd)
for base in "$HOME/snap/kicad/current/.local/share/kicad/9.0" "$HOME/.local/share/kicad/9.0"; do
  if [ -d "$base" ]; then
    mkdir -p "$base/scripting/plugins"
    ln -sfn "$here/plugin" "$base/scripting/plugins/trouting_action"
    echo "linked $base/scripting/plugins/trouting_action -> $here/plugin"
    exit 0
  fi
done
echo "no KiCad 9 user folder found (start KiCad once first)" >&2
exit 1
