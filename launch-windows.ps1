#Requires -Version 5.1
<#
  Odysseus - native Windows launcher (no Docker).

  One command to: create a virtualenv, install dependencies, run first-time
  setup (prints an admin password on first run), and start the server.
  Safe to re-run - it skips whatever already exists.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1
    powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1 -Port 7000 -BindHost 127.0.0.1

  Tip: bind 127.0.0.1 (default) for local-only use. Use 0.0.0.0 only when you
  intentionally want other devices on your LAN to reach it.
#>
param(
    [int]$Port = 7000,
    [string]$BindHost = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Step($msg) { Write-Host ""; Write-Host ("==> " + $msg) -ForegroundColor Cyan }
function Fail($msg) {
    Write-Host ""
    Write-Host ("ERROR: " + $msg) -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

function Test-WindowsBashStub($path) {
    if (-not $path) { return $false }
    $lowered = $path.ToLowerInvariant()
    foreach ($stub in @("system32\bash.exe", "sysnative\bash.exe", "windowsapps\bash.exe")) {
        if ($lowered.Contains($stub)) { return $true }
    }
    return $false
}

function Test-PortOpen($Hostname, $port, $timeoutSeconds = 2) {
    # Map 0.0.0.0 and localhost to the explicit IPv4 loopback
    if ($Hostname -eq "0.0.0.0" -or $Hostname -eq "localhost") { $Hostname = "127.0.0.1" }
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $async = $client.BeginConnect($Hostname, $port, $null, $null)
        if ($async.AsyncWaitHandle.WaitOne([int]($timeoutSeconds * 1000))) {
            $client.EndConnect($async)
            $client.Close()
            return $true
        }
        $client.Close()
        return $false
    } catch {
        return $false
    }
}

function Find-ChromaExe {
    $candidate = Join-Path $PSScriptRoot "venv\Scripts\chroma.exe"
    if (Test-Path $candidate) { return $candidate }
    $cmd = Get-Command chroma.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Ensure-FullChromaDBPackage {
    if (-not (Find-ChromaExe)) {
        Write-Step "Installing full ChromaDB package (required for local Windows launcher)..."
        & $venvPy -m pip install chromadb
        if ($LASTEXITCODE -ne 0) {
            Fail "Failed to install ChromaDB. Scroll up for pip error details."
        }
    }

    $oldErrPref = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $importTest = & $venvPy -c "import chromadb" 2>&1
    $ErrorActionPreference = $oldErrPref

    if ($LASTEXITCODE -ne 0) { 
        Fail "ChromaDB failed to import. Error details:`n$importTest`n`nEnsure the Visual C++ Redistributable is installed." 
    }
}

function Start-ChromaDB {
    param(
        [string]$Hostname = "127.0.0.1",
        [int]$Port = 8100,
        [string]$Path = "$PSScriptRoot\data\chroma"
    )

    if (Test-PortOpen $Hostname $Port 1) {
        Write-Host "ChromaDB already listening on ${Hostname}:${Port}"
        return
    }

    $logDir = Join-Path $PSScriptRoot "logs"
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $chromaOut = Join-Path $logDir "chroma-out.log"
    $chromaErr = Join-Path $logDir "chroma-err.log"

    $chromaExe = Find-ChromaExe
    if (-not $chromaExe) { Fail "Could not find chroma.exe after installing chromadb." }

    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    Write-Step "Starting ChromaDB service on ${Hostname}:${Port}"
    
    # Start chroma directly and capture the process object
    $script:chromaProcess = Start-Process -FilePath $chromaExe -ArgumentList "run", "--host", $Hostname, "--port", $Port, "--path", $Path -WindowStyle Hidden -RedirectStandardOutput $chromaOut -RedirectStandardError $chromaErr -PassThru
    
    Start-Sleep -Seconds 2

    $maxWait = 30 # Increased timeout
    $elapsed = 0
    while (-not (Test-PortOpen $Hostname $Port 1) -and $elapsed -lt $maxWait) {
        Start-Sleep -Seconds 1
        $elapsed += 1
    }

    # If the port is not open, the process likely failed. Capture the logs.
    if (-not (Test-PortOpen $Hostname $Port 1)) {
        $logPathRelative = "logs\chroma-err.log"
        Fail "ChromaDB did not start on ${Hostname}:${Port} after $maxWait seconds. Check the log file for errors: $logPathRelative"
    }

    Write-Host "ChromaDB listening on ${Hostname}:${Port}"
}

function Find-GitBash {
    $cmd = Get-Command bash -ErrorAction SilentlyContinue
    if ($cmd -and -not (Test-WindowsBashStub $cmd.Source)) { return $cmd.Source }

    $roots = @()
    foreach ($name in @("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LocalAppData")) {
        $base = [Environment]::GetEnvironmentVariable($name)
        if ($base) {
            $roots += (Join-Path $base "Git")
            if ($name -eq "LocalAppData") { $roots += (Join-Path $base "Programs\Git") }
        }
    }
    $roots += @("C:\Program Files\Git", "C:\Program Files (x86)\Git")

    foreach ($root in ($roots | Select-Object -Unique)) {
        foreach ($relative in @("bin\bash.exe", "usr\bin\bash.exe")) {
            $candidate = Join-Path $root $relative
            if (Test-Path $candidate) { return $candidate }
        }
    }
    return $null
}

# 1. Locate a Python interpreter (3.11+ required)
Write-Step "Checking for Python"
function Get-PythonVersionText($launcher, $launcherArgs) {
    try {
        return (& $launcher @launcherArgs -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null).Trim()
    } catch {
        return $null
    }
}

$pyExe = $null
$pyArgs = @()
$pyVersion = $null

$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    foreach ($v in @("-3.13", "-3.12", "-3.11")) {
        $ver = Get-PythonVersionText $pyLauncher.Source @($v)
        if ($ver) {
            $pyExe = $pyLauncher.Source
            $pyArgs = @($v)
            $pyVersion = $ver
            break
        }
    }
}

if (-not $pyExe) {
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        $ver = Get-PythonVersionText $pythonCmd.Source @()
        if ($ver -and $ver.Contains('.')) {
            $versionParts = $ver.Split('.')
            $major = [int]$versionParts[0]
            $minor = [int]$versionParts[1]
            if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11)) {
                $pyExe = $pythonCmd.Source
                $pyVersion = $ver
            }
        }
    }
}

if ($pyExe -like "*WindowsApps*python.exe") {
    $pyCmd = Get-Command py -ErrorAction SilentlyContinue
    if ($pyCmd) {
        $pyExe = $pyCmd.Source
        $pyArgs = @("-3.11")
    }
}

if (-not $pyExe) {
    Fail "Couldn't find Python 3.11+ for Windows setup. Install Python 3.11+ (or open the Python launcher with 'py -3.11') from https://www.python.org/downloads/, then re-run this script."
}
$pythonLabel = ("Using Python {0}: {1} {2}" -f $pyVersion, $pyExe, ($pyArgs -join ' ')).TrimEnd()
Write-Host $pythonLabel

# 2. Create the virtualenv if missing
$venvPy = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Step "Creating virtual environment (venv)"
    & $pyExe @pyArgs -m venv venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) { Fail "Failed to create the virtual environment." }
} else {
    Write-Host "venv already exists - skipping creation."
}

# 3. Install / update dependencies
Write-Step "Installing dependencies (first run can take a few minutes)"
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail "Dependency install failed. Scroll up for the pip error." }

# Clean up any potential chromadb-client conflict before starting the server.
Ensure-FullChromaDBPackage

# 4. First-time setup (creates data dirs, DB, .env, admin user)
Write-Step "Running first-time setup"
& $venvPy setup.py
if ($LASTEXITCODE -ne 0) { Fail "setup.py failed." }

# 5. Friendly note about Git Bash (full Cookbook / agent-shell parity)
if (-not (Find-GitBash)) {
    Write-Host ""
    Write-Host "NOTE: Git Bash (bash.exe) was not found on PATH." -ForegroundColor Yellow
    Write-Host "      The core app works without it. For full Cookbook background" -ForegroundColor Yellow
    Write-Host "      downloads and the agent shell tool, install Git for Windows:" -ForegroundColor Yellow
    Write-Host "      https://git-scm.com/download/win" -ForegroundColor Yellow
}

# 6. Point CUDA_PATH at a real CUDA toolkit so GPU llama-cpp-python can import.
$cudaBase = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"
if (Test-Path $cudaBase) {
    $cudaBest = Get-ChildItem $cudaBase -Directory -ErrorAction SilentlyContinue |
        Where-Object { Test-Path (Join-Path $_.FullName "bin") } |
        Sort-Object { try { [version]($_.Name -replace "^v", "") } catch { [version]"0.0" } } -Descending |
        Select-Object -First 1
    if ($cudaBest) {
        $env:CUDA_PATH = $cudaBest.FullName
        Write-Host ("Using CUDA_PATH = " + $cudaBest.FullName) -ForegroundColor Cyan
    }
}

# 7. Start the server (use `python -m uvicorn` - bare `uvicorn` may not be on PATH)
if (Test-PortOpen $BindHost $Port 1) {
    Fail "Port $Port is already in use. Please choose a different port or kill the existing process."
}

$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*CHROMADB_HOST\s*=\s*["'']?([^#"''\s]+)') { $env:CHROMADB_HOST = $Matches[1].Trim() }
        if ($line -match '^\s*CHROMADB_PORT\s*=\s*["'']?([^#"''\s]+)') { $env:CHROMADB_PORT = $Matches[1].Trim() }
    }
}

$chromaHost = $env:CHROMADB_HOST
if (-not $chromaHost -or $chromaHost -eq "localhost") { $chromaHost = "127.0.0.1" }
$chromaPort = $env:CHROMADB_PORT
if (-not $chromaPort) { $chromaPort = "8100" }

Start-ChromaDB -Hostname $chromaHost -Port $chromaPort -Path (Join-Path $PSScriptRoot "data\chroma")
$env:CHROMADB_HOST = $chromaHost
$env:CHROMADB_PORT = $chromaPort

Write-Step ("Starting Odysseus at http://{0}:{1}" -f $BindHost, $Port)
Write-Host "Press Ctrl+C to stop."
Write-Host ""

try {
    & $venvPy -m uvicorn app:app --host $BindHost --port $Port
} finally {
    if ($script:chromaProcess) {
        Write-Host "`nStopping ChromaDB background process..." -ForegroundColor Cyan
        Stop-Process -Id $script:chromaProcess.Id -Force -ErrorAction SilentlyContinue
    }
}