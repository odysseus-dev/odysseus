# Test-only reproduction of the old hidden launch chain, including risky cleanup.
param([string]$Python, [int]$Port, [int]$GatewayPort, [int]$ChromaPort,
      [int]$SidecarPid, [string]$StateDirectory)
$ErrorActionPreference = 'Stop'
if ($Port -in @(7000,9130,8100) -or $GatewayPort -in @(7000,9130,8100) -or $ChromaPort -in @(7000,9130,8100)) { throw 'Production ports forbidden' }
Set-Location (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent)
$env:ODYSSEUS_RESTART_LAB_GATEWAY_PORT = "$GatewayPort"
$env:ODYSSEUS_RESTART_LAB_DIR = $StateDirectory
try {
    & $Python -m uvicorn tests.helpers.windows_restart_fixture:app --host 127.0.0.1 --port $Port --log-level warning
} finally {
    # The bootstrap rehearsal must bypass this only by terminating OUR lab
    # supervisor first, never by tree killing or touching production.
    Stop-Process -Id $SidecarPid -Force -ErrorAction SilentlyContinue
}
