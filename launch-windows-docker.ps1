#Requires -Version 5.1
<#
  Odysseus - one-click Windows launcher for the Docker deployment.

  What it does:
    - Starts Docker Desktop if it is not already running.
    - Runs `docker compose up -d` for the Odysseus stack.
    - Waits for http://localhost:7000 to respond.
    - Opens the app in your default browser.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\launch-windows-docker.ps1
#>

param(
    [string]$ProjectRoot = $PSScriptRoot,
    [int]$Port = 7000,
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
Set-Location -Path $ProjectRoot

function Write-Step($msg) { Write-Host ""; Write-Host ("==> " + $msg) -ForegroundColor Cyan }
function Fail($msg) {
    Write-Host ""
    Write-Host ("ERROR: " + $msg) -ForegroundColor Red
    exit 1
}

function Test-HttpReady([string]$Url) {
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        return ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500)
    } catch {
        return $false
    }
}

function Start-DockerDesktop {
    $desktop = @(
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
        "$env:ProgramFiles(x86)\Docker\Docker\Docker Desktop.exe"
    ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

    if (-not $desktop) {
        return $false
    }

    if (-not (Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue)) {
        Write-Step "Starting Docker Desktop"
        Start-Process -FilePath $desktop -WindowStyle Hidden | Out-Null
    }
    return $true
}

function Wait-ForDocker([int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            docker info | Out-Null
            return $true
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    return $false
}

Write-Step "Checking Docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker CLI was not found on PATH."
}

Start-DockerDesktop | Out-Null
if (-not (Wait-ForDocker -Seconds $TimeoutSeconds)) {
    Fail "Docker did not become ready within $TimeoutSeconds seconds."
}

Write-Step "Starting Odysseus stack"
# Build from the local app bundle before starting. This keeps installed
# frontend features in sync with the Docker image that actually serves them.
docker compose up -d --build | Out-Host
if ($LASTEXITCODE -ne 0) {
    Fail "docker compose up failed."
}

$url = "http://127.0.0.1:$Port"
Write-Step "Waiting for Odysseus at $url"
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while (-not (Test-HttpReady $url)) {
    if ((Get-Date) -ge $deadline) {
        Fail "Odysseus did not respond at $url within $TimeoutSeconds seconds."
    }
    Start-Sleep -Seconds 2
}

Write-Step "Opening browser"
Start-Process $url | Out-Null
Write-Host ""
Write-Host "Odysseus is ready at $url" -ForegroundColor Green
