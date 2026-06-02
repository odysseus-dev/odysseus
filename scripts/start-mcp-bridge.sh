#!/bin/bash
# Start the Tech Duinn MCP Bridge
# This allows Claude Code, OpenCode, and OpenClaw to share the same swarm

set -e
cd "$(dirname "$0")/.."

TOKEN_FILE="data/tech_duinn/.bridge_token"
if [ ! -f "$TOKEN_FILE" ]; then
    TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    mkdir -p "$(dirname "$TOKEN_FILE")"
    echo "$TOKEN" > "$TOKEN_FILE"
    echo "Generated new auth token: $TOKEN"
else
    TOKEN=$(cat "$TOKEN_FILE")
    echo "Using existing auth token"
fi

PORT="${MCP_BRIDGE_PORT:-8378}"

echo "Starting Tech Duinn MCP Bridge on port $PORT"
echo "  SSE:      http://localhost:$PORT/sse"
echo "  REST:     http://localhost:$PORT/api/health"
echo "  Token:    $TOKEN"
echo ""

exec python3 mcp_bridge.py --port "$PORT" --token "$TOKEN"
