#!/usr/bin/env pwsh
# Start Docker MCP Gateway with a known auth token.
# Called by Start-Odysseus.ps1 or run standalone.
#
# Usage: .\scripts\start-mcp-gateway.ps1
# Env out: MCP_GATEWAY_AUTH_TOKEN=***REDACTED***

$env:MCP_GATEWAY_AUTH_TOKEN = "***REDACTED***"
docker mcp gateway run --transport streaming --port 9090 --servers playwright,fetch --long-lived
