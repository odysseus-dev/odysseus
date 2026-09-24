#!/bin/sh
# Run overlay Native chat probe on the Fusion guest Docker, never Mac Docker.
# Overlay compose lives on orchestration-vm (hhpe-forge). Mac docker.sock is empty.

set -eu

GUEST_HOST="${OVERLAY_GUEST_HOST:-orchestration-vm}"
GUEST_DIR="${OVERLAY_GUEST_DIR:-/home/agent/work/odysseus}"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.openhands.yml"
INNER="exec -T odysseus python3 /app/scripts/overlay_native_chat_probe.py"

if [ -f /home/agent/work/odysseus/docker-compose.openhands.yml ]; then
  export PATH="${HOME}/.local/bin:${PATH}"
  cd /home/agent/work/odysseus
  exec ${COMPOSE} ${INNER}
fi

exec ssh -o BatchMode=yes "${GUEST_HOST}" \
  "export PATH=\"\$HOME/.local/bin:\$PATH\"; cd ${GUEST_DIR} && ${COMPOSE} ${INNER}"
