#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_FILE="$SCRIPT_DIR/juniperus-ui.service"

if [ ! -f "$SERVICE_FILE" ]; then
  echo "Error: juniperus-ui.service not found in $SCRIPT_DIR"
  exit 1
fi

echo "Installing Juniperus UI service..."
echo "Make sure you've edited juniperus-ui.service with your username and paths first!"
echo ""

sudo cp "$SERVICE_FILE" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable juniperus-ui
sudo systemctl start juniperus-ui
sudo systemctl status juniperus-ui
