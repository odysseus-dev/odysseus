#Requires -Version 5.1
<#
  Start Odysseus bundled sidecars only (ChromaDB, SearXNG, ntfy).
  Safe to re-run. Does NOT start the odysseus app container.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\scripts\start-sidecars.ps1
#>
$ErrorActionPreference = "Stop"
Set-Location -Path (Split-Path -Parent $PSScriptRoot)

function Write-Step($msg) { Write-Host ""; Write-Host ("==> " + $msg) -ForegroundColor Cyan }
function Fail($msg) {
    Write-Host ("ERROR: " + $msg) -ForegroundColor Red
    exit 1
}

Write-Step "Starting Docker sidecars (chromadb, searxng, ntfy)"
docker compose up -d chromadb searxng ntfy
if ($LASTEXITCODE -ne 0) { Fail "docker compose up failed (exit $LASTEXITCODE)" }

Write-Step "Waiting for sidecars to become reachable"
$deadline = (Get-Date).AddMinutes(3)
while ((Get-Date) -lt $deadline) {
    $chromadb = Test-NetConnection -ComputerName 127.0.0.1 -Port 8100 -WarningAction SilentlyContinue
    $searxng  = Test-NetConnection -ComputerName 127.0.0.1 -Port 8080 -WarningAction SilentlyContinue
    $ntfy     = Test-NetConnection -ComputerName 127.0.0.1 -Port 8091 -WarningAction SilentlyContinue
    if ($chromadb.TcpTestSucceeded -and $searxng.TcpTestSucceeded -and $ntfy.TcpTestSucceeded) {
        Write-Host "All sidecar ports open on loopback." -ForegroundColor Green
        docker compose ps chromadb searxng ntfy
        exit 0
    }
    Start-Sleep -Seconds 3
}
Fail "Sidecars did not become reachable within 3 minutes. Check: docker compose logs searxng"
