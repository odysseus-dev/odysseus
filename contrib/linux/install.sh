#!/bin/bash

APPS_DIR="$HOME/.local/share/applications"
ICONS_DIR="$HOME/.local/share/icons"
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )" || exit 1

# ensure XDG target directories exist
mkdir -p "$APPS_DIR"
mkdir -p "$ICONS_DIR"

# copy the custom icon to local system icon pool (default colors from webpage)
cp "$SCRIPT_DIR/icon.svg" "$ICONS_DIR/odysseus.svg"

# ensure launch script is executable
chmod +x "$SCRIPT_DIR/launch.sh"

# generate the customized application shortcut desktop file
cat << INNER_EOF > "$APPS_DIR/odysseus.desktop"
[Desktop Entry]
Version=1.0
Type=Application
Name=Odysseus
Comment=Your own AI Workspace, running on your hardware
Exec=$SCRIPT_DIR/launch.sh
Icon=odysseus
Terminal=true
Categories=Development;IDE;
INNER_EOF

echo "[o] You can now launch Odysseus from your system's launcher."
