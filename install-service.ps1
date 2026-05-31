# install-service.ps1 — Install Odysseus as a Windows Service using NSSM
#
# Usage:
#   .\install-service.ps1                    # install with defaults
#   .\install-service.ps1 -Uninstall         # remove the service
#   .\install-service.ps1 -Port 8080         # custom port
#   .\install-service.ps1 -IPAddress "0.0.0.0" # bind to all interfaces
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
    [string]$IPAddress = "0.0.0.0",
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

# --- Determine paths ---
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -Path $ProjectDir

# --- Check & Request Administrator Privileges ---
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Administrator privileges required. Requesting elevation..." -ForegroundColor Yellow
    $argsList = @("-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    if ($PSBoundParameters.Count -gt 0) {
        foreach ($key in $PSBoundParameters.Keys) {
            $value = $PSBoundParameters[$key]
            if ($value -is [switch]) {
                if ($value.IsPresent) {
                    $argsList += "-$key"
                }
            } else {
                $argsList += "-$key"
                $argsList += "`"$value`""
            }
        }
    }
    Start-Process powershell -ArgumentList $argsList -Verb RunAs
    exit
}

$VenvDir = Join-Path $ProjectDir "venv"
$UvicornExe = Join-Path $VenvDir "Scripts\uvicorn.exe"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"
$PipExe = Join-Path $VenvDir "Scripts\pip.exe"
$LogDir = Join-Path $ProjectDir "data\logs"

# --- Check & Setup Python Virtual Environment ---
if (-not (Test-Path $UvicornExe)) {
    Write-Host "Virtual environment not found or incomplete. Automating setup..." -ForegroundColor Yellow
    
    # Check if python is available
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCmd) {
        Write-Host "ERROR: Python is not installed or not in system PATH." -ForegroundColor Red
        exit 1
    }
    
    Write-Host "Creating virtual environment in $VenvDir..." -ForegroundColor Cyan
    & python -m venv "$VenvDir"
    
    if (-not (Test-Path $PythonExe)) {
        Write-Host "ERROR: Failed to create virtual environment." -ForegroundColor Red
        exit 1
    }
    
    Write-Host "Installing dependencies from requirements.txt..." -ForegroundColor Cyan
    & $PipExe install -r (Join-Path $ProjectDir "requirements.txt")
    
    Write-Host "Running setup.py..." -ForegroundColor Cyan
    & $PythonExe (Join-Path $ProjectDir "setup.py")
    
    if (-not (Test-Path $UvicornExe)) {
        Write-Host "ERROR: uvicorn still not found in virtual environment after setup." -ForegroundColor Red
        exit 1
    }
}

# --- Check & Setup NSSM ---
$NssmBin = "nssm"
$globalNssm = Get-Command nssm -ErrorAction SilentlyContinue
if (-not $globalNssm) {
    # Check for local nssm.exe in project dir
    $localNssm = Join-Path $ProjectDir "nssm.exe"
    if (Test-Path $localNssm) {
        $NssmBin = $localNssm
    } else {
        Write-Host "NSSM not found in system PATH or local project directory." -ForegroundColor Yellow
        Write-Host "Automating NSSM download..." -ForegroundColor Cyan
        
        # Ensure TLS 1.2 is used
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        
        $zipUrl = "https://nssm.cc/release/nssm-2.24.zip"
        $zipPath = Join-Path $ProjectDir "nssm.zip"
        $tempExtractDir = Join-Path $ProjectDir "nssm-temp"
        
        try {
            Write-Host "Downloading $zipUrl..." -ForegroundColor Gray
            Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
            
            Write-Host "Extracting nssm.exe..." -ForegroundColor Gray
            Expand-Archive -Path $zipPath -DestinationPath $tempExtractDir -Force
            
            $extractedNssm = Join-Path $tempExtractDir "nssm-2.24\win64\nssm.exe"
            if (Test-Path $extractedNssm) {
                Copy-Item -Path $extractedNssm -Destination $localNssm -Force
                $NssmBin = $localNssm
                Write-Host "Successfully downloaded and set up local nssm.exe." -ForegroundColor Green
            } else {
                Write-Host "ERROR: Could not find win64\nssm.exe in the extracted archive." -ForegroundColor Red
                exit 1
            }
        }
        catch {
            Write-Host "ERROR: Failed to download or extract NSSM: $_" -ForegroundColor Red
            exit 1
        }
        finally {
            # Cleanup
            if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
            if (Test-Path $tempExtractDir) { Remove-Item $tempExtractDir -Recurse -Force }
        }
    }
}

# --- Uninstall ---
if ($Uninstall) {
    Write-Host "Stopping service $ServiceName..." -ForegroundColor Yellow
    $oldEAP = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        & $NssmBin stop $ServiceName 2>$null
    } catch {}
    Write-Host "Removing service $ServiceName..." -ForegroundColor Yellow
    try {
        & $NssmBin remove $ServiceName confirm 2>$null
    } catch {}
    $ErrorActionPreference = $oldEAP
    Write-Host "Service $ServiceName removed." -ForegroundColor Green
    exit 0
}

# --- Install ---
Write-Host "Installing Odysseus as Windows Service..." -ForegroundColor Cyan
Write-Host "  Service name: $ServiceName"
Write-Host "  Bind:         ${IPAddress}:${Port}"
Write-Host "  Project dir:  $ProjectDir"
Write-Host "  Uvicorn:      $UvicornExe"
Write-Host ""

# Create log directory
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Check if service already exists
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Service $ServiceName already exists. Stopping and reconfiguring..." -ForegroundColor Yellow
    $oldEAP = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        & $NssmBin stop $ServiceName 2>$null
    } catch {}
    try {
        & $NssmBin remove $ServiceName confirm 2>$null
    } catch {}
    $ErrorActionPreference = $oldEAP
}

# Install the service
$AppArgs = "app:app --host $IPAddress --port $Port --workers 1"
& $NssmBin install $ServiceName $UvicornExe $AppArgs

# Configure working directory
& $NssmBin set $ServiceName AppDirectory $ProjectDir

# Configure logging
$StdoutLog = Join-Path $LogDir "odysseus-stdout.log"
$StderrLog = Join-Path $LogDir "odysseus-stderr.log"
& $NssmBin set $ServiceName AppStdout $StdoutLog
& $NssmBin set $ServiceName AppStderr $StderrLog
& $NssmBin set $ServiceName AppStdoutCreationDisposition 4  # Append
& $NssmBin set $ServiceName AppStderrCreationDisposition 4  # Append
& $NssmBin set $ServiceName AppRotateFiles 1
& $NssmBin set $ServiceName AppRotateBytes 10485760  # 10MB per log file

# Configure restart on crash
& $NssmBin set $ServiceName AppRestartDelay 5000  # 5 second delay before restart

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
        & $NssmBin set $ServiceName AppEnvironmentExtra ($envVars -join "`n")
    }
}

# Set display name and description
& $NssmBin set $ServiceName DisplayName "Odysseus UI"
& $NssmBin set $ServiceName Description "Odysseus AI Chat Application - Self-hosted AI assistant"
& $NssmBin set $ServiceName Start SERVICE_AUTO_START

# Start the service
Write-Host ""
Write-Host "Starting service..." -ForegroundColor Cyan
& $NssmBin start $ServiceName

Start-Sleep -Seconds 2

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq "Running") {
    Write-Host ""
    Write-Host "Odysseus is running!" -ForegroundColor Green
    Write-Host "  URL:     http://localhost:$Port" -ForegroundColor White
    Write-Host "  Status:  $NssmBin status $ServiceName" -ForegroundColor Gray
    Write-Host "  Logs:    $LogDir" -ForegroundColor Gray
    Write-Host "  Stop:    $NssmBin stop $ServiceName" -ForegroundColor Gray
    Write-Host "  Remove:  .\install-service.ps1 -Uninstall" -ForegroundColor Gray
} else {
    Write-Host "WARNING: Service may not have started. Check:" -ForegroundColor Yellow
    Write-Host "  $NssmBin status $ServiceName"
    Write-Host "  Get-Content '$StderrLog' -Tail 20"
}
