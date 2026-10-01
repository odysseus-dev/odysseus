#!/usr/bin/env bash
# Frontend-only Canvas: proxy Agent Server + Automation already running in Compose.
# The upstream image entrypoint starts nested copies of both backends; that would
# split conversations away from the unpublished Agent Server Odysseus talks to.
set -euo pipefail

PORT="${PORT:-8000}"
AGENT_SERVER_URL="${AGENT_SERVER_URL:-http://openhands-agent-server:8000}"
AUTOMATION_URL="${AUTOMATION_URL:-http://openhands-automation:8000}"
AGENT_CANVAS_BASE_PATH="${AGENT_CANVAS_BASE_PATH:-/canvas}"
SESSION_API_KEY="${SESSION_API_KEY:-${OH_SESSION_API_KEYS_0:-local-dev-session}}"

case "$AGENT_CANVAS_BASE_PATH" in
  /*) ;;
  *) AGENT_CANVAS_BASE_PATH="/${AGENT_CANVAS_BASE_PATH}" ;;
esac
AGENT_CANVAS_BASE_PATH="${AGENT_CANVAS_BASE_PATH%/}"
if [ -z "$AGENT_CANVAS_BASE_PATH" ]; then
  AGENT_CANVAS_BASE_PATH="/canvas"
fi

RUNTIME_SERVICES_INFO="$(
  node /opt/agent-canvas/runtime-services-info.mjs \
    --mode docker \
    --agent-host-alias openhands-agent-server \
    --agent-server-url "$AGENT_SERVER_URL" \
    --automation-url "$AUTOMATION_URL"
)"

exec node /opt/agent-canvas/static-server.mjs \
  --port "$PORT" \
  --host 0.0.0.0 \
  --dir /opt/agent-canvas/frontend \
  --base-path "$AGENT_CANVAS_BASE_PATH" \
  --session-api-key "$SESSION_API_KEY" \
  --runtime-services-info "$RUNTIME_SERVICES_INFO" \
  --route "/api/automation=${AUTOMATION_URL}" \
  --route "/api=${AGENT_SERVER_URL}" \
  --route "/server_info=${AGENT_SERVER_URL}" \
  --route "/sockets=${AGENT_SERVER_URL}" \
  --route "/alive=${AGENT_SERVER_URL}" \
  --route "/health=${AGENT_SERVER_URL}" \
  --route "/ready=${AGENT_SERVER_URL}" \
  --route "/docs=${AGENT_SERVER_URL}" \
  --route "/redoc=${AGENT_SERVER_URL}" \
  --route "/openapi.json=${AGENT_SERVER_URL}"
