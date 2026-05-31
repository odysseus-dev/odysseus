#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PLIST_TEMPLATE="$SCRIPT_DIR/com.pewdiepie.odysseus.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.pewdiepie.odysseus.plist"

if [ ! -f "$PLIST_TEMPLATE" ]; then
  echo "Error: com.pewdiepie.odysseus.plist not found in $SCRIPT_DIR"
  exit 1
fi

echo "Installing Odysseus LaunchAgent for macOS..."

# Replace __WORKSPACE__ placeholder with actual path and write to destination
sed "s|__WORKSPACE__|$PROJECT_DIR|g" "$PLIST_TEMPLATE" > "$PLIST_DEST"

# Set correct permissions
chmod 644 "$PLIST_DEST"

# Unload existing agent if loaded
launchctl unload "$PLIST_DEST" 2>/dev/null || true

# Load the agent
launchctl load "$PLIST_DEST"

echo "Odysseus LaunchAgent loaded successfully."
echo "The backend server will run automatically in the background on port 7007."
echo "You can check its status using: launchctl list | grep odysseus"
