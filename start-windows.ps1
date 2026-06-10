#Requires -Version 5.1
<#
.SYNOPSIS
    Odysseus — one-command quick start for Windows (native).

.DESCRIPTION
    Installs everything Odysseus needs, sets up a local Python environment,
    and launches the app — so a generic Windows user can run it without
    knowing anything about venvs, pip, or uvicorn. Safe to re-run; it skips
    work that's already done.

    Usage:
        powershell -ExecutionPolicy Bypass -File .\start-windows.ps1

    Or with custom port/host:
        powershell -ExecutionPolicy Bypass -File .\start-windows.ps1 -Port 7900 -Host 0.0.0.0

    Why native (not Docker): Running natively provides better performance and
    direct access to system resources. Docker on Windows adds virtualization overhead.

.PARAMETER Port
    The port to run Odysseus on. If not specified, reads from environment variables
    ODYSSEUS_PORT, then APP_PORT from .env, or defaults to 7860.

.PARAMETER Host
    The host to bind to. If not specified, reads from environment variables
    ODYSSEUS_HOST, then APP_BIND from .env, or defaults to 127.0.0.1.
    Set to 0.0.0.0 for LAN/network access.

.PARAMETER NoOpen
    Skip automatically opening the browser when the server is ready.

.EXAMPLE
    .\start-windows.ps1
    Start Odysseus with default settings (reads .env if present)

.EXAMPLE
    .\start-windows.ps1 -Port 7900 -Host 0.0.0.0
    Start on port 7900, accessible from network

.EXAMPLE
    .\start-windows.ps1 -NoOpen
    Start without opening browser automatically
#>

param(
    [int]$Port,
    [string]$Host,
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
$RepoDir = $PSScriptRoot
Set-Location -Path $RepoDir

# ============================================================================
# Helper Functions
# ============================================================================

function Write-Step($msg) {
    Write-Host ""
    Write-Host "▶ $msg" -ForegroundColor Cyan
}

function Write-Success($msg) {
    Write-Host "  ✓ $msg" -ForegroundColor Green
}

function Write-Warning2($msg) {
    Write-Host "  ⚠ $msg" -ForegroundColor Yellow
}

function Fail($msg) {
    Write-Host ""
    Write-Host "✗ $msg" -ForegroundColor Red
    Write-Host ""
    Write-Host "It is safe to re-run .\start-windows.ps1" -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

function Load-EnvFile {
    <#
    .SYNOPSIS
        Load .env file and set environment variables (only if not already set)
    #>
    $envFile = Join-Path $RepoDir ".env"
    if (Test-Path $envFile) {
        Get-Content $envFile | ForEach-Object {
            $line = $_.Trim()
            # Skip empty lines and comments
            if ($line -and -not $line.StartsWith('#')) {
                # Split on first = sign
                if ($line -match '^([^=]+)=(.*)$') {
                    $key = $matches[1].Trim()
                    $value = $matches[2].Trim()
                    # Remove inline comments
                    $value = ($value -split '#')[0].Trim()
                    # Remove quotes if present
                    $value = $value.Trim('"').Trim("'")
                    # Only set if not already in environment
                    if (-not (Test-Path "env:$key")) {
                        [Environment]::SetEnvironmentVariable($key, $value, "Process")
                    }
                }
            }
        }
    }
}

function Test-PortInUse($portNum, $hostAddr) {
    <#
    .SYNOPSIS
        Check if a port is already in use
    #>
    try {
        $testHost = $hostAddr
        if ($testHost -eq "0.0.0.0" -or $testHost -eq "::") {
            $testHost = "127.0.0.1"
        }
        $tcpClient = New-Object System.Net.Sockets.TcpClient
        $tcpClient.Connect($testHost, $portNum)
        $tcpClient.Close()
        return $true
    } catch {
        return $false
    }
}

function Get-PythonVersionText($launcher, $launcherArgs) {
    <#
    .SYNOPSIS
        Get Python version as string (e.g., "3.11.5")
    #>
    try {
        $version = (& $launcher @launcherArgs -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $version) {
            return $version.Trim()
        }
        return $null
    } catch {
        return $null
    }
}

function Test-PythonVersion($launcher, $launcherArgs) {
    <#
    .SYNOPSIS
        Check if Python version is 3.11+
    #>
    try {
        $result = (& $launcher @launcherArgs -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)" 2>$null)
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Find-GitBash {
    <#
    .SYNOPSIS
        Locate Git Bash on Windows (needed for Cookbook background tasks)
    #>
    $cmd = Get-Command bash -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $roots = @()
    foreach ($name in @("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LocalAppData")) {
        $base = [Environment]::GetEnvironmentVariable($name)
        if ($base) { $roots += (Join-Path $base "Git") }
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

function Get-FileHash-MD5($filePath) {
    <#
    .SYNOPSIS
        Get MD5 hash of a file
    #>
    if (Test-Path $filePath) {
        $hash = Get-FileHash -Path $filePath -Algorithm MD5
        return $hash.Hash.ToLower()
    }
    return ""
}

# ============================================================================
# Main Setup Flow
# ============================================================================

Write-Host ""
Write-Host "▶ Odysseus quick start for Windows" -ForegroundColor Cyan
Write-Host ""

# Load .env file first so APP_PORT and APP_BIND are available
Load-EnvFile

# Determine port and host (priority: CLI args > env vars > .env > defaults)
if (-not $Port) {
    if ($env:ODYSSEUS_PORT) {
        $Port = [int]$env:ODYSSEUS_PORT
    } elseif ($env:APP_PORT) {
        $Port = [int]$env:APP_PORT
    } else {
        $Port = 7860  # Default port (not 7000 — various services use that)
    }
}

if (-not $Host) {
    if ($env:ODYSSEUS_HOST) {
        $Host = $env:ODYSSEUS_HOST
    } elseif ($env:APP_BIND) {
        $Host = $env:APP_BIND
    } else {
        $Host = "127.0.0.1"  # Localhost by default; use 0.0.0.0 for LAN access
    }
}

# Check if port is already in use
if (Test-PortInUse -portNum $Port -hostAddr $Host) {
    Fail "Port $Port is already in use on $Host. Stop what's using it, or pick another port:`n    .\start-windows.ps1 -Port 7900"
}

# ============================================================================
# 1. Find Python 3.11+
# ============================================================================

Write-Step "Checking for Python 3.11+"

$pyExe = $null
$pyArgs = @()
$pyVersion = $null

# Try py launcher first (preferred on Windows)
$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    foreach ($v in @("-3.13", "-3.12", "-3.11")) {
        if (Test-PythonVersion $pyLauncher.Source @($v)) {
            $ver = Get-PythonVersionText $pyLauncher.Source @($v)
            if ($ver) {
                $pyExe = $pyLauncher.Source
                $pyArgs = @($v)
                $pyVersion = $ver
                break
            }
        }
    }
}

# Fallback to direct python command
if (-not $pyExe) {
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        if (Test-PythonVersion $pythonCmd.Source @()) {
            $pyVersion = Get-PythonVersionText $pythonCmd.Source @()
            if ($pyVersion) {
                $pyExe = $pythonCmd.Source
                $pyArgs = @()
            }
        }
    }
}

if (-not $pyExe) {
    Fail "Couldn't find Python 3.11+ for Windows setup.`nInstall Python 3.11+ from https://www.python.org/downloads/`nThen re-run this script."
}

$pythonLabel = "using Python $pyVersion"
if ($pyArgs) {
    $pythonLabel += " (via py $($pyArgs -join ' '))"
}
Write-Host "  ($pythonLabel at $pyExe)"

# ============================================================================
# 2. Check Dependencies
# ============================================================================

Write-Step "Checking dependencies…"

# Git Bash (needed for Cookbook background tasks, but not required to boot)
$gitBash = Find-GitBash
if ($gitBash) {
    Write-Success "Git Bash found at $gitBash"
} else {
    Write-Warning2 "Git Bash not found — Cookbook (local model serving) may be limited."
    Write-Host "    You can install it from: https://git-scm.com/download/win" -ForegroundColor Yellow
}

# ============================================================================
# 3. Python Virtual Environment
# ============================================================================

$venvPy = Join-Path $RepoDir "venv\Scripts\python.exe"

if (-not (Test-Path $venvPy)) {
    Write-Step "Creating Python environment…"
    & $pyExe @pyArgs -m venv venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) {
        Fail "Failed to create the virtual environment."
    }
} else {
    Write-Host "  Python environment exists — skipping creation"
}

# Install/update dependencies only if requirements.txt changed
$reqFile = Join-Path $RepoDir "requirements.txt"
$reqHash = Get-FileHash-MD5 $reqFile
$reqHashFile = Join-Path $RepoDir "venv\.requirements_hash"
$storedHash = ""
if (Test-Path $reqHashFile) {
    $storedHash = (Get-Content $reqHashFile -Raw).Trim()
}

if ($reqHash -ne $storedHash) {
    Write-Step "Installing Python packages (first run can take a few minutes)…"
    & $venvPy -m pip install --quiet --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        Fail "Failed to upgrade pip."
    }
    # Show progress for the slow step
    & $venvPy -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Fail "Dependency install failed. Check the error above."
    }
    # Save hash to skip reinstall on next run
    Set-Content -Path $reqHashFile -Value $reqHash -NoNewline
} else {
    Write-Step "Python packages up to date — skipping install"
}

# Clean up chromadb-client conflict (HTTP-only package conflicts with full chromadb)
$chromaClientInstalled = & $venvPy -m pip show chromadb-client 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Step "Cleaning up conflicting chromadb-client package…"
    & $venvPy -m pip uninstall -y chromadb-client
    & $venvPy -m pip install --force-reinstall chromadb
}

# ============================================================================
# 4. First-Run Setup
# ============================================================================

Write-Step "Preparing Odysseus…"
$env:ODYSSEUS_SKIP_RUN_HINT = "1"
& $venvPy setup.py
if ($LASTEXITCODE -ne 0) {
    Fail "setup.py failed."
}

# ============================================================================
# 5. Launch Server
# ============================================================================

# Determine URL for browser
$urlHost = $Host
if ($urlHost -eq "0.0.0.0" -or $urlHost -eq "::") {
    $urlHost = "127.0.0.1"
}
$url = "http://${urlHost}:${Port}"

# Background job to open browser once server is ready
$browserJob = $null
if (-not $NoOpen -and -not $env:ODYSSEUS_NO_OPEN) {
    $browserJob = Start-Job -ScriptBlock {
        param($targetHost, $targetPort, $targetUrl)
        $testHost = $targetHost
        if ($testHost -eq "0.0.0.0" -or $testHost -eq "::") {
            $testHost = "127.0.0.1"
        }

        # Poll for up to 90 seconds
        for ($i = 0; $i -lt 90; $i++) {
            try {
                $tcpClient = New-Object System.Net.Sockets.TcpClient
                $tcpClient.Connect($testHost, $targetPort)
                $tcpClient.Close()

                # Server is up - signal success
                return @{
                    Ready = $true
                    Url = $targetUrl
                }
            } catch {
                Start-Sleep -Seconds 1
            }
        }
        return @{ Ready = $false }
    } -ArgumentList $Host, $Port, $url
}

Write-Host ""
Write-Step "Starting Odysseus — it will open in your browser at $url"
if ($Host -eq "0.0.0.0") {
    Write-Host "  (LAN/network access enabled)" -ForegroundColor Yellow
}
Write-Host "  (this takes a few seconds; press Ctrl+C here to stop)"
Write-Host ""

# Start the server (this blocks until Ctrl+C)
try {
    # Launch server in background to monitor browser opener job
    $serverJob = Start-Job -ScriptBlock {
        param($venvPath, $bindHost, $bindPort)
        Set-Location $using:RepoDir
        & $venvPath -m uvicorn app:app --host $bindHost --port $bindPort
    } -ArgumentList $venvPy, $Host, $Port

    $browserOpened = $false

    # Monitor both jobs
    while ($serverJob.State -eq 'Running') {
        # Check browser opener job
        if ($browserJob -and -not $browserOpened) {
            if ($browserJob.State -eq 'Completed') {
                $result = Receive-Job -Job $browserJob
                if ($result.Ready) {
                    Write-Host ""
                    Write-Host "  ┌────────────────────────────────────────────┐" -ForegroundColor Green
                    Write-Host "  │  ✓ Odysseus is ready — opening your browser  │" -ForegroundColor Green
                    Write-Host ("  │     {0,-40} │" -f $result.Url) -ForegroundColor Green
                    Write-Host "  │     (Press Ctrl+C in this window to stop)    │" -ForegroundColor Green
                    Write-Host "  └────────────────────────────────────────────┘" -ForegroundColor Green
                    Write-Host ""
                    Start-Process $result.Url
                }
                Remove-Job -Job $browserJob
                $browserOpened = $true
            }
        }

        # Show server output
        $serverJob | Receive-Job | ForEach-Object { Write-Host $_ }

        Start-Sleep -Milliseconds 100
    }

    # Get any remaining output
    $serverJob | Receive-Job | ForEach-Object { Write-Host $_ }

    # Clean up
    if ($browserJob) { Remove-Job -Job $browserJob -Force -ErrorAction SilentlyContinue }
    Remove-Job -Job $serverJob -Force -ErrorAction SilentlyContinue

} catch {
    Write-Host ""
    Write-Host "Server stopped." -ForegroundColor Yellow
    if ($browserJob) { Remove-Job -Job $browserJob -Force -ErrorAction SilentlyContinue }
} finally {
    # Cleanup on exit
    if ($browserJob) {
        Stop-Job -Job $browserJob -ErrorAction SilentlyContinue
        Remove-Job -Job $browserJob -Force -ErrorAction SilentlyContinue
    }
    if ($serverJob) {
        Stop-Job -Job $serverJob -ErrorAction SilentlyContinue
        Remove-Job -Job $serverJob -Force -ErrorAction SilentlyContinue
    }
}

