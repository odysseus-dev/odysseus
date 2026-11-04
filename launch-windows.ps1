#Requires -Version 5.1
<#
  Odysseus - native Windows launcher (no Docker).

  One command to: create a virtualenv, install dependencies, run first-time
  setup (prints an admin password on first run), start a local ChromaDB server
  for vector memory and document RAG, and start the server.
  Safe to re-run - it skips whatever already exists.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1
    powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1 -Port 7000 -BindHost 127.0.0.1
    powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1 -NoChroma

  Tip: bind 127.0.0.1 (default) for local-only use. Use 0.0.0.0 only when you
  intentionally want other devices on your LAN to reach it.
#>
param(
    [int]$Port = 7000,
    [string]$BindHost = "127.0.0.1",
    # Skip the local ChromaDB server (vector memory and RAG stay degraded
    # unless CHROMADB_HOST points at a server you run yourself).
    [switch]$NoChroma
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
        if ($ver) {
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
& $venvPy -m pip install --quiet --require-hashes --only-binary=:all: --no-deps -r requirements-bootstrap.txt
if ($LASTEXITCODE -ne 0) { Fail "TLS dependency bootstrap failed." }
& $venvPy -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail "Dependency install failed. Scroll up for the pip error." }

# 4. First-time setup (creates data dirs, DB, .env, admin user)
Write-Step "Running first-time setup"
& $venvPy setup.py
if ($LASTEXITCODE -ne 0) { Fail "setup.py failed." }

# 5. Local ChromaDB server for vector memory + document RAG.
# Docker Compose runs ChromaDB next to the app; a native install has nothing
# listening on CHROMADB_PORT, so memory and RAG come up degraded. The full
# chromadb package (which provides `chroma run`) cannot share a venv with the
# HTTP-only chromadb-client the app uses, so it gets its own venv, pinned to
# the client's version.
function Get-DotEnvValue($name) {
    if (Test-Path ".env") {
        foreach ($line in Get-Content ".env") {
            if ($line -match ("^\s*" + $name + "\s*=\s*(.*)$")) { return $Matches[1].Trim().Trim('"').Trim("'") }
        }
    }
    return $null
}

function Test-TcpPort($hostName, $portNumber) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connect = $client.BeginConnect($hostName, $portNumber, $null, $null)
        if (-not $connect.AsyncWaitHandle.WaitOne(500)) { return $false }
        $client.EndConnect($connect)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

$chromaProc = $null
$chromaHost = $env:CHROMADB_HOST
if (-not $chromaHost) { $chromaHost = Get-DotEnvValue "CHROMADB_HOST" }
if (-not $chromaHost) { $chromaHost = "localhost" }
$chromaPort = $env:CHROMADB_PORT
if (-not $chromaPort) { $chromaPort = Get-DotEnvValue "CHROMADB_PORT" }
if (-not $chromaPort) { $chromaPort = "8100" }
$chromaPort = [int]$chromaPort
$chromaBindHost = "127.0.0.1"
if ($chromaHost -in @("::1", "[::1]")) {
    $chromaBindHost = "::1"
    # chromadb-client constructs an HTTP URL from this host. An IPv6 literal
    # needs brackets there, even though the server's bind address does not.
    $env:CHROMADB_HOST = "[::1]"
}

if ($NoChroma) {
    Write-Host ""
    Write-Host "Skipping the local ChromaDB server (-NoChroma)." -ForegroundColor Yellow
} elseif ($chromaHost -notin @("localhost", "127.0.0.1", "::1", "[::1]")) {
    Write-Host ("CHROMADB_HOST is {0} - not starting a local ChromaDB server." -f $chromaHost)
} elseif (Test-TcpPort $chromaBindHost $chromaPort) {
    Write-Host ("ChromaDB already listening on port {0} - using it." -f $chromaPort)
} else {
    Write-Step "Starting the local ChromaDB server (vector memory + document RAG)"
    $chromaVersion = (& $venvPy -c "import importlib.metadata as m; print(m.version('chromadb-client'))" 2>$null)
    if ($chromaVersion) { $chromaVersion = $chromaVersion.Trim() }
    $chromaVenvPy = Join-Path $PSScriptRoot "venv-chroma\Scripts\python.exe"
    $chromaExe = Join-Path $PSScriptRoot "venv-chroma\Scripts\chroma.exe"
    $chromaReady = $true
    if (-not (Test-Path $chromaVenvPy)) {
        & $venvPy -m venv venv-chroma
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $chromaVenvPy)) { $chromaReady = $false }
    }
    if ($chromaReady) {
        $installed = (& $chromaVenvPy -c "import importlib.metadata as m; print(m.version('chromadb'))" 2>$null)
        if ($installed) { $installed = $installed.Trim() }
        if (-not (Test-Path $chromaExe) -or ($chromaVersion -and $installed -ne $chromaVersion)) {
            $spec = "chromadb"
            if ($chromaVersion) { $spec = "chromadb==" + $chromaVersion }
            Write-Host ("Installing {0} into venv-chroma (first run only)..." -f $spec)
            & $chromaVenvPy -m pip install --quiet --upgrade pip
            & $chromaVenvPy -m pip install --quiet $spec
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path $chromaExe)) { $chromaReady = $false }
        }
    }
    if ($chromaReady) {
        New-Item -ItemType Directory -Force -Path "logs" | Out-Null
        $chromaProc = Start-Process -FilePath $chromaExe -PassThru -WindowStyle Hidden `
            -ArgumentList @("run", "--host", $chromaBindHost, "--port", $chromaPort, "--path", "data\chroma") `
            -RedirectStandardOutput "logs\chroma.log" -RedirectStandardError "logs\chroma.err.log"
        $deadline = (Get-Date).AddSeconds(60)
        while (-not (Test-TcpPort $chromaBindHost $chromaPort) -and -not $chromaProc.HasExited -and (Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 500
        }
        if (Test-TcpPort $chromaBindHost $chromaPort) {
            Write-Host ("ChromaDB running on {0}:{1} (data in data\chroma, log in logs\chroma.log)." -f $chromaBindHost, $chromaPort)
        } else {
            $chromaReady = $false
            if (-not $chromaProc.HasExited) { Stop-Process -Id $chromaProc.Id -Force -ErrorAction SilentlyContinue }
            $chromaProc = $null
        }
    }
    if (-not $chromaReady) {
        Write-Host ""
        Write-Host "WARNING: could not start a local ChromaDB server (see logs\chroma.err.log)." -ForegroundColor Yellow
        Write-Host "         Odysseus still starts, but vector memory and document RAG stay" -ForegroundColor Yellow
        Write-Host "         degraded until a ChromaDB server is reachable on CHROMADB_PORT." -ForegroundColor Yellow
    }
}

# 6. Friendly note about Git Bash (full Cookbook / agent-shell parity)
if (-not (Find-GitBash)) {
    Write-Host ""
    Write-Host "NOTE: Git Bash (bash.exe) was not found on PATH." -ForegroundColor Yellow
    Write-Host "      The core app works without it. For full Cookbook background" -ForegroundColor Yellow
    Write-Host "      downloads and the agent shell tool, install Git for Windows:" -ForegroundColor Yellow
    Write-Host "      https://git-scm.com/download/win" -ForegroundColor Yellow
}

# 7. Point CUDA_PATH at a real CUDA toolkit so GPU llama-cpp-python can import.
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

# 8. Start the server (use `python -m uvicorn` - bare `uvicorn` may not be on PATH)
# -Port only reaches uvicorn as a flag. Everything that builds a URL for this
# instance - internal_api_base(), companion pairing, the MCP OAuth callback -
# reads APP_PORT, so set it too or they all assume 7000.
$env:APP_PORT = $Port
Write-Step ("Starting Odysseus at http://{0}:{1}" -f $BindHost, $Port)
Write-Host "Press Ctrl+C to stop."
Write-Host ""
try {
    & $venvPy -m uvicorn app:app --host $BindHost --port $Port
} finally {
    if ($chromaProc -and -not $chromaProc.HasExited) {
        Stop-Process -Id $chromaProc.Id -Force -ErrorAction SilentlyContinue
    }
}
