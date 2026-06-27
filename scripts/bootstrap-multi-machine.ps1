#Requires -Version 5.1
<#
  Bootstrap .env for a multi-machine Odysseus workstation (Tailscale + Radicale).

  Creates .env from .env.example if missing, appends a commented multi-machine
  block, and runs scripts/multi_machine_env.py to validate required keys.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap-multi-machine.ps1
    powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap-multi-machine.ps1 -RadicaleUrl "http://100.64.0.10:5232/alice/"
#>
param(
    [string]$RadicaleUrl = "",
    [string[]]$LlmHosts = @()
)

$ErrorActionPreference = "Stop"
Set-Location -Path (Split-Path -Parent $PSScriptRoot)

function Write-Step($msg) { Write-Host ""; Write-Host ("==> " + $msg) -ForegroundColor Cyan }

if (-not (Test-Path ".env")) {
    Write-Step "Creating .env from .env.example"
    Copy-Item ".env.example" ".env"
} else {
    Write-Step ".env already exists — appending multi-machine block if absent"
}

$tailscaleIp = ""
$tailscaleCmd = Get-Command tailscale -ErrorAction SilentlyContinue
if ($tailscaleCmd) {
    try {
        $tailscaleIp = (& tailscale ip -4 2>$null | Select-Object -First 1).Trim()
    } catch {
        $tailscaleIp = ""
    }
}

$block = @"

# ============================================================
# Multi-machine (Tailscale + Radicale) — added by bootstrap-multi-machine.ps1
# See docs/multi-machine.md
# ============================================================
# Remote model hosts on your tailnet (comma-separated Tailscale MagicDNS names or IPs)
# LLM_HOSTS=desktop-gpu, laptop

# Host inference (Docker: use host.docker.internal; native: localhost)
# LM_STUDIO_URL=http://host.docker.internal:1234
# OLLAMA_BASE_URL=http://host.docker.internal:11434/v1

# Required when CalDAV/Radicale uses Tailscale or RFC1918 addresses
ODYSSEUS_ALLOW_PRIVATE_CALDAV=1

# Shared Radicale on NAS (Settings → Calendar → CalDAV uses the same URL)
# RADICALE_URL=http://100.64.0.10:5232/alice/
"@

if ($tailscaleIp) {
    $block += "`n# Detected Tailscale IP on this machine: $tailscaleIp"
    $block += "`n# NTFY_BIND=$tailscaleIp"
    $block += "`n# NTFY_BASE_URL=http://${tailscaleIp}:8091"
}

if ($RadicaleUrl) {
    $block += "`nRADICALE_URL=$RadicaleUrl"
}

if ($LlmHosts.Count -gt 0) {
    $joined = ($LlmHosts -join ", ")
    $block += "`nLLM_HOSTS=$joined"
}

$existing = Get-Content ".env" -Raw -ErrorAction SilentlyContinue
if ($existing -notmatch "Multi-machine \(Tailscale \+ Radicale\)") {
    Add-Content -Path ".env" -Value $block -Encoding utf8
    Write-Host "Appended multi-machine block to .env"
} else {
    Write-Host "Multi-machine block already present in .env"
}

Write-Step "Validating multi-machine .env"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot "venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $python) {
    Write-Host "WARN: Python not found — skip multi_machine_env check" -ForegroundColor Yellow
    exit 0
}

$checker = Join-Path $PSScriptRoot "multi_machine_env.py"
& $python $checker --env-file (Join-Path (Get-Location) ".env")
exit $LASTEXITCODE
