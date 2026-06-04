#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_FILE="$SCRIPT_DIR/odysseus-ui.service"

if [ ! -f "$SERVICE_FILE" ]; then
  echo "Error: odysseus-ui.service not found in $SCRIPT_DIR"
  exit 1
fi

echo "Installing Odysseus UI service..."
echo "Make sure you've edited odysseus-ui.service with your username and paths first!"
echo ""

sudo cp "$SERVICE_FILE" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable odysseus-ui
sudo systemctl start odysseus-ui
sudo systemctl status odysseus-ui
