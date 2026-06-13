#Requires -Version 5.1
<#
  Start Odysseus Docker sidecars for native Windows installs.
  Usage: powershell -ExecutionPolicy Bypass -File .\scripts\start-sidecars.ps1
#>
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "Docker not found. Install Docker Desktop, then re-run."
}
docker compose version | Out-Null

Write-Host "Starting ChromaDB, SearXNG, ntfy..."
docker compose up -d chromadb searxng ntfy

$deadline = (Get-Date).AddMinutes(2)
while ((Get-Date) -lt $deadline) {
    $chroma = Test-NetConnection -ComputerName 127.0.0.1 -Port 8100 -WarningAction SilentlyContinue
    $searx = Test-NetConnection -ComputerName 127.0.0.1 -Port 8080 -WarningAction SilentlyContinue
    if ($chroma.TcpTestSucceeded -and $searx.TcpTestSucceeded) {
        Write-Host "Sidecars ready: ChromaDB :8100, SearXNG :8080, ntfy :8091"
        exit 0
    }
    Start-Sleep -Seconds 2
}
Write-Warning "Sidecars started but ports not open yet. Check: docker compose ps"
exit 1
