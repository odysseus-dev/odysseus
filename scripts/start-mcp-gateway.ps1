#!/usr/bin/env pwsh
# Start Docker MCP Gateway with auth token read from .env file.
# Called by Start-Odysseus.ps1 or run standalone.
#
# Usage: .\scripts\start-mcp-gateway.ps1
# Requires: MCP_GATEWAY_AUTH_TOKEN set in .env (root of repo) or already in env.

$envFile = Join-Path $PSScriptRoot '..' '.env'
if (Test-Path $envFile) {
    Get-Content $envFile | Where-Object { $_ -match '^\s*MCP_GATEWAY_AUTH_TOKEN\s*=' } | ForEach-Object {
        $val = ($_ -split '=', 2)[1].Trim()
        $env:MCP_GATEWAY_AUTH_TOKEN = $val
    }
}
if (-not $env:MCP_GATEWAY_AUTH_TOKEN) {
    Write-Error 'MCP_GATEWAY_AUTH_TOKEN not set. Add it to .env (see .env.example).'
    exit 1
}
docker mcp gateway run --transport streaming --port 9090 --servers playwright,fetch,filesystem --long-lived
