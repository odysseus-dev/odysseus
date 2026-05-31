# install-service.ps1 — Install Odysseus as a Windows Service using NSSM
#
# Usage:
#   .\install-service.ps1                    # install with defaults
#   .\install-service.ps1 -Uninstall         # remove the service
#   .\install-service.ps1 -Port 8080         # custom port
#   .\install-service.ps1 -Host "0.0.0.0"   # bind to all interfaces
#
# Prerequisites:
#   1. Python venv with Odysseus dependencies installed
#   2. NSSM (https://nssm.cc) — download and place nssm.exe in your PATH
#
# NSSM handles:
#   - Auto-restart on crash
#   - Log rotation (stdout/stderr → data\logs\)
#   - Environment variables (reads from .env)
#   - Service start on boot

param(
    [string]$ServiceName = "OdysseusUI",
    [string]$Host = "0.0.0.0",
    [int]$Port = 7000,
    [switch]$Uninstall,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

if ($Help) {
    Write-Host @"
Odysseus Windows Service Installer (NSSM)

USAGE:
    .\install-service.ps1                     Install with defaults (port 7000)
    .\install-service.ps1 -Port 8080          Custom port
    .\install-service.ps1 -Uninstall          Remove the service
    .\install-service.ps1 -Help               Show this help

PREREQUISITES:
    1. Install NSSM from https://nssm.cc/download
       Extract nssm.exe to a directory in your PATH (e.g. C:\Windows\System32)
    2. Set up Odysseus in a Python virtual environment:
       python -m venv venv
       .\venv\Scripts\Activate.ps1
       pip install -r requirements.txt
       python setup.py
"@
    exit 0
}

# --- Check NSSM ---
$nssmPath = Get-Command nssm -ErrorAction SilentlyContinue
if (-not $nssmPath) {
    Write-Host "ERROR: NSSM not found in PATH." -ForegroundColor Red
    Write-Host ""
    Write-Host "Install NSSM (Non-Sucking Service Manager):" -ForegroundColor Yellow
    Write-Host "  1. Download from https://nssm.cc/download"
    Write-Host "  2. Extract nssm.exe to a directory in your PATH"
    Write-Host "     (e.g. copy nssm.exe to C:\Windows\System32)"
    Write-Host "  3. Re-run this script"
    exit 1
}

# --- Determine paths ---
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $ProjectDir "venv"
$UvicornExe = Join-Path $VenvDir "Scripts\uvicorn.exe"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"
$LogDir = Join-Path $ProjectDir "data\logs"

# Check venv exists
if (-not (Test-Path $UvicornExe)) {
    # Try using global uvicorn
    $UvicornExe = (Get-Command uvicorn -ErrorAction SilentlyContinue).Source
    if (-not $UvicornExe) {
        Write-Host "ERROR: uvicorn not found." -ForegroundColor Red
        Write-Host "Create a virtual environment first:" -ForegroundColor Yellow
        Write-Host "  python -m venv venv"
        Write-Host "  .\venv\Scripts\Activate.ps1"
        Write-Host "  pip install -r requirements.txt"
        exit 1
    }
}

# --- Uninstall ---
if ($Uninstall) {
    Write-Host "Stopping service $ServiceName..." -ForegroundColor Yellow
    nssm stop $ServiceName 2>$null
    Write-Host "Removing service $ServiceName..." -ForegroundColor Yellow
    nssm remove $ServiceName confirm
    Write-Host "Service $ServiceName removed." -ForegroundColor Green
    exit 0
}

# --- Install ---
Write-Host "Installing Odysseus as Windows Service..." -ForegroundColor Cyan
Write-Host "  Service name: $ServiceName"
Write-Host "  Bind:         ${Host}:${Port}"
Write-Host "  Project dir:  $ProjectDir"
Write-Host "  Uvicorn:      $UvicornExe"
Write-Host ""

# Create log directory
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Check if service already exists
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Service $ServiceName already exists. Stopping and reconfiguring..." -ForegroundColor Yellow
    nssm stop $ServiceName 2>$null
    nssm remove $ServiceName confirm 2>$null
}

# Install the service
$AppArgs = "app:app --host $Host --port $Port --workers 1"
nssm install $ServiceName $UvicornExe $AppArgs

# Configure working directory
nssm set $ServiceName AppDirectory $ProjectDir

# Configure logging
$StdoutLog = Join-Path $LogDir "odysseus-stdout.log"
$StderrLog = Join-Path $LogDir "odysseus-stderr.log"
nssm set $ServiceName AppStdout $StdoutLog
nssm set $ServiceName AppStderr $StderrLog
nssm set $ServiceName AppStdoutCreationDisposition 4  # Append
nssm set $ServiceName AppStderrCreationDisposition 4  # Append
nssm set $ServiceName AppRotateFiles 1
nssm set $ServiceName AppRotateBytes 10485760  # 10MB per log file

# Configure restart on crash
nssm set $ServiceName AppRestartDelay 5000  # 5 second delay before restart

# Load .env file if present
$EnvFile = Join-Path $ProjectDir ".env"
if (Test-Path $EnvFile) {
    Write-Host "Loading environment from .env..." -ForegroundColor Gray
    $envVars = @()
    foreach ($line in Get-Content $EnvFile) {
        $line = $line.Trim()
        if ($line -and -not $line.StartsWith("#")) {
            $envVars += $line
        }
    }
    if ($envVars.Count -gt 0) {
        nssm set $ServiceName AppEnvironmentExtra ($envVars -join "`n")
    }
}

# Set display name and description
nssm set $ServiceName DisplayName "Odysseus UI"
nssm set $ServiceName Description "Odysseus AI Chat Application - Self-hosted AI assistant"
nssm set $ServiceName Start SERVICE_AUTO_START

# Start the service
Write-Host ""
Write-Host "Starting service..." -ForegroundColor Cyan
nssm start $ServiceName

Start-Sleep -Seconds 2

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq "Running") {
    Write-Host ""
    Write-Host "Odysseus is running!" -ForegroundColor Green
    Write-Host "  URL:     http://localhost:$Port" -ForegroundColor White
    Write-Host "  Status:  nssm status $ServiceName" -ForegroundColor Gray
    Write-Host "  Logs:    $LogDir" -ForegroundColor Gray
    Write-Host "  Stop:    nssm stop $ServiceName" -ForegroundColor Gray
    Write-Host "  Remove:  .\install-service.ps1 -Uninstall" -ForegroundColor Gray
} else {
    Write-Host "WARNING: Service may not have started. Check:" -ForegroundColor Yellow
    Write-Host "  nssm status $ServiceName"
    Write-Host "  Get-Content '$StderrLog' -Tail 20"
}
