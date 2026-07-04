#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_FILE="$SCRIPT_DIR/void-ui.service"

if [ ! -f "$SERVICE_FILE" ]; then
  echo "Error: void-ui.service not found in $SCRIPT_DIR"
  exit 1
fi

echo "Installing Void UI service..."
echo "Make sure you've edited void-ui.service with your username and paths first!"
echo ""

sudo cp "$SERVICE_FILE" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable void-ui
sudo systemctl start void-ui
sudo systemctl status void-ui
