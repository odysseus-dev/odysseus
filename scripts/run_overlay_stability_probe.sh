#!/bin/sh
# Run the overlay stability probe on Fusion guest Docker, never Mac Docker.
# Three compose files: base, OpenHands, observability. Never relay.
# Overlay compose lives on orchestration-vm (hhpe-forge). Mac docker.sock is empty.

set -eu

GUEST_HOST="${OVERLAY_GUEST_HOST:-orchestration-vm}"
GUEST_DIR="${OVERLAY_GUEST_DIR:-/home/agent/work/odysseus}"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.openhands.yml -f docker-compose.observability.yml"

# Stdlib parser: ConfigFiles for the compose project whose files live in $PWD.
# No single quotes, so the same text can be passed to a remote `python3 -c`.
PARSE='import json,os,sys
cwd=os.path.realpath(os.getcwd())
raw=sys.stdin.read().strip() or "[]"
try:
 data=json.loads(raw)
except json.JSONDecodeError:
 data=[]
if isinstance(data, dict):
 data=[data]
chosen=[]
for item in data:
 paths=[p.strip() for p in str(item.get("ConfigFiles") or "").split(",") if p.strip()]
 dirs=set()
 for p in paths:
  try:
   dirs.add(os.path.realpath(os.path.dirname(p)))
  except OSError:
   dirs.add(os.path.dirname(p))
 if paths and cwd in dirs:
  chosen=paths
  break
print(",".join(chosen))'

if [ -f /home/agent/work/odysseus/docker-compose.openhands.yml ]; then
  export PATH="${HOME}/.local/bin:${PATH}"
  cd /home/agent/work/odysseus
  files=$(${COMPOSE} ls --format json | python3 -c "${PARSE}" || true)
  exec ${COMPOSE} exec -T -e "OVERLAY_COMPOSE_CONFIG_FILES=${files}" odysseus python3 /app/scripts/overlay_stability_probe.py
fi

exec ssh -o BatchMode=yes "${GUEST_HOST}" \
  "export PATH=\"\$HOME/.local/bin:\$PATH\"; cd ${GUEST_DIR} && files=\$(${COMPOSE} ls --format json | python3 -c '${PARSE}' || true) && ${COMPOSE} exec -T -e OVERLAY_COMPOSE_CONFIG_FILES=\"\$files\" odysseus python3 /app/scripts/overlay_stability_probe.py"
