#Requires -Version 5.1
<#
.SYNOPSIS
    Start Odysseus via Docker Compose, with first-run .env setup.
#>

# Keep the window open no matter what — trap everything
try {

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

# ── Preflight ─────────────────────────────────────────────────────────────────

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: Docker not found on PATH. Install Docker Desktop and try again." -ForegroundColor Red
    Read-Host "`nPress Enter to exit"
    exit 1
}

Write-Host "Checking Docker daemon..." -ForegroundColor Cyan
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker daemon is not running." -ForegroundColor Yellow
    $answer = Read-Host "Start Docker Desktop now? (Y/n)"
    if ($answer -ine "n") {
        $dockerDesktop = "${env:ProgramFiles}\Docker\Docker\Docker Desktop.exe"
        if (-not (Test-Path $dockerDesktop)) {
            $dockerDesktop = "${env:LOCALAPPDATA}\Docker\Docker Desktop.exe"
        }
        if (Test-Path $dockerDesktop) {
            Write-Host "Starting Docker Desktop..." -ForegroundColor Cyan
            Start-Process $dockerDesktop
            Write-Host "Waiting for Docker daemon to be ready..." -ForegroundColor Cyan
            $waited = 0
            while ($waited -lt 60) {
                Start-Sleep -Seconds 3
                $waited += 3
                docker info 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) { break }
                Write-Host "  ...waiting ($waited s)" -ForegroundColor DarkGray
            }
            docker info 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Host "ERROR: Docker still not ready after 60s. Try launching Docker Desktop manually." -ForegroundColor Red
                Read-Host "`nPress Enter to exit"
                exit 1
            }
        } else {
            Write-Host "ERROR: Docker Desktop executable not found. Please start it manually." -ForegroundColor Red
            Read-Host "`nPress Enter to exit"
            exit 1
        }
    } else {
        Read-Host "`nPress Enter to exit"
        exit 1
    }
}
Write-Host "Docker is running." -ForegroundColor Green
Write-Host ""

# ── .env setup ────────────────────────────────────────────────────────────────

if (-not (Test-Path ".env")) {
    Write-Host ".env not found — creating from .env.example..." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"

    (Get-Content ".env") `
        -replace '^LLM_HOST=.*', 'LLM_HOST=host.docker.internal' |
        Set-Content ".env"

    Write-Host ".env created. LLM_HOST set to host.docker.internal (reaches your host machine from inside Docker)." -ForegroundColor Green
    Write-Host ""
}

# ── Pull latest images ─────────────────────────────────────────────────────────

Write-Host "Pulling latest images..." -ForegroundColor Cyan
docker compose -f docker-compose.windows.yml pull
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: Could not pull latest images (offline?). Using cached versions." -ForegroundColor Yellow
}
Write-Host ""

# ── Build app image & start ───────────────────────────────────────────────────

Write-Host "Building and starting Odysseus (Windows port layout)..." -ForegroundColor Cyan
Write-Host "  odysseus  -> http://localhost:7400" -ForegroundColor DarkGray
Write-Host "  chromadb  -> http://localhost:7401" -ForegroundColor DarkGray
Write-Host "  searxng   -> http://localhost:7402" -ForegroundColor DarkGray
Write-Host "  ntfy      -> http://localhost:7403" -ForegroundColor DarkGray
Write-Host ""
docker compose -f docker-compose.windows.yml up -d --build
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: docker compose up failed. See output above." -ForegroundColor Red
    Read-Host "`nPress Enter to exit"
    exit 1
}
Write-Host ""

# ── Wait for the app to be ready ──────────────────────────────────────────────

Write-Host "Waiting for Odysseus to be ready..." -ForegroundColor Cyan
$url     = "http://localhost:7400"
$timeout = 90
$elapsed = 0
$ready   = $false

while ($elapsed -lt $timeout) {
    try {
        $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        if ($resp.StatusCode -lt 500) { $ready = $true; break }
    } catch { }
    Start-Sleep -Seconds 2
    $elapsed += 2
    Write-Host "  ...still starting ($elapsed s / ${timeout}s)" -ForegroundColor DarkGray
}

Write-Host ""
if ($ready) {
    Write-Host "Odysseus is up!  ->  $url" -ForegroundColor Green
    Start-Process $url
} else {
    Write-Host "App did not respond within ${timeout}s. Last 40 log lines:" -ForegroundColor Yellow
    Write-Host ""
    docker compose -f docker-compose.windows.yml logs --tail 40 odysseus
}

# ── Handy commands ────────────────────────────────────────────────────────────

Write-Host ""
$f = "-f docker-compose.windows.yml"
Write-Host "Useful commands:" -ForegroundColor DarkGray
Write-Host "  docker compose $f logs -f odysseus   # live logs" -ForegroundColor DarkGray
Write-Host "  docker compose $f stop               # stop without removing" -ForegroundColor DarkGray
Write-Host "  docker compose $f down               # stop and remove containers" -ForegroundColor DarkGray
Write-Host "  docker compose $f down -v            # also wipe volumes (all data!)" -ForegroundColor DarkGray

} catch {
    Write-Host ""
    Write-Host "UNEXPECTED ERROR: $_" -ForegroundColor Red
}

Read-Host "`nPress Enter to exit"
