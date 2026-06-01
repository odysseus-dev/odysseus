#!/usr/bin/env bash
# update-watch.sh — Host-side watcher for in-app update pulls.
#
# The Odysseus update panel (Settings → System → Software Update) writes
# data/update_ready after a successful git pull. This script polls for that
# file and runs `docker compose up --build -d` when it appears, then removes
# the file so the rebuild only fires once per pull.
#
# Run it on the HOST (not inside the container), in the repo directory where
# docker-compose.yml lives.
#
# ── Automated use ────────────────────────────────────────────────────────────
#
# Option 1 — crontab (every 2 minutes):
#   crontab -e
#   Add: */2 * * * * /path/to/odysseus/scripts/update-watch.sh >> /var/log/odysseus-update.log 2>&1
#
# Option 2 — systemd timer (recommended for persistent setups):
#   1. Copy this script somewhere persistent, e.g. /opt/odysseus/update-watch.sh
#   2. Create /etc/systemd/system/odysseus-update-watch.service:
#
#       [Unit]
#       Description=Odysseus in-app update watcher
#       After=docker.service
#
#       [Service]
#       Type=oneshot
#       WorkingDirectory=/opt/odysseus
#       ExecStart=/opt/odysseus/scripts/update-watch.sh
#       StandardOutput=journal
#       StandardError=journal
#
#   3. Create /etc/systemd/system/odysseus-update-watch.timer:
#
#       [Unit]
#       Description=Run Odysseus update watcher every 2 minutes
#
#       [Timer]
#       OnBootSec=2min
#       OnUnitActiveSec=2min
#
#       [Install]
#       WantedBy=timers.target
#
#   4. Enable: systemctl daemon-reload && systemctl enable --now odysseus-update-watch.timer
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

TRIGGER_FILE="${ODYSSEUS_DATA_DIR:-data}/update_ready"
COMPOSE_CMD="${DOCKER_COMPOSE_CMD:-docker compose}"

if [ ! -f "$TRIGGER_FILE" ]; then
    exit 0
fi

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] update_ready detected — rebuilding container"

# Remove the trigger before rebuilding so a failed rebuild doesn't loop.
rm -f "$TRIGGER_FILE"

$COMPOSE_CMD up --build -d

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Rebuild complete"
