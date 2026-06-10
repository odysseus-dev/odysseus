# Odysseus Windows Installer
# One-click setup: creates venv, installs deps, runs first-time setup, starts server.
# Usage: Right-click -> "Run with PowerShell" or: powershell -ExecutionPolicy Bypass -File install-windows.ps1

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

Write-Host ""
Write-Host "=== Odysseus Windows Installer ===" -ForegroundColor Cyan
Write-Host ""

# Check Python
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "[ERROR] Python not found. Install Python 3.11+ from https://www.python.org/downloads/" -ForegroundColor Red
    Write-Host "        Make sure to check 'Add python.exe to PATH' during installation." -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}
$pyVer = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
Write-Host "[OK] Python $pyVer found" -ForegroundColor Green

$major, $minor = $pyVer -split '\.'
if ([int]$major -lt 3 -or ([int]$major -eq 3 -and [int]$minor -lt 11)) {
    Write-Host "[ERROR] Python 3.11+ required (found $pyVer)" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# Create venv
if (-not (Test-Path "venv\Scripts\python.exe")) {
    Write-Host ""
    Write-Host "1. Creating virtual environment..." -ForegroundColor Cyan
    python -m venv venv
    Write-Host "[OK] Virtual environment created" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "1. Virtual environment already exists" -ForegroundColor Green
}

# Activate and install deps
Write-Host ""
Write-Host "2. Installing dependencies..." -ForegroundColor Cyan
& venv\Scripts\pip.exe install --upgrade pip -q
& venv\Scripts\pip.exe install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Failed to install dependencies" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "[OK] Dependencies installed" -ForegroundColor Green

# Optional deps
Write-Host ""
Write-Host "3. Installing optional dependencies..." -ForegroundColor Cyan
& venv\Scripts\pip.exe install -r requirements-optional.txt -q 2>$null
Write-Host "[OK] Optional dependencies installed (some may have been skipped)" -ForegroundColor Green

# Run setup.py
Write-Host ""
Write-Host "4. Running first-time setup..." -ForegroundColor Cyan
& venv\Scripts\python.exe setup.py

# Create start script
$startScript = @'
@echo off
cd /d "%~dp0"
echo Starting Odysseus...
echo.
echo Open http://localhost:7000 in your browser
echo Press Ctrl+C to stop the server
echo.
venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 7000
pause
'@
Set-Content -Path "start-odysseus.bat" -Value $startScript -NoNewline

Write-Host ""
Write-Host "=== Installation Complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "To start Odysseus:" -ForegroundColor Yellow
Write-Host "  Double-click start-odysseus.bat" -ForegroundColor White
Write-Host "  Or run: venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 7000" -ForegroundColor White
Write-Host ""
Write-Host "Then open http://localhost:7000 in your browser." -ForegroundColor White
Write-Host ""

$start = Read-Host "Start Odysseus now? (Y/n)"
if ($start -ne "n" -and $start -ne "N") {
    Write-Host ""
    Write-Host "Starting Odysseus on http://localhost:7000 ..." -ForegroundColor Cyan
    Write-Host "Press Ctrl+C to stop." -ForegroundColor Yellow
    Write-Host ""
    & venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 7000
}
