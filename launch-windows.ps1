#Requires -Version 5.1
<#
  Odysseus - native Windows launcher (no Docker).

  One command to: install uv locally, install a uv-managed Python locally,
  create a virtualenv, install dependencies, run first-time setup
  (prints an admin password on first run), and start the server.

  Safe to re-run - it skips whatever already exists.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1
    powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1 -Port 7000 -BindHost 127.0.0.1

  If you already created venv using system Python and want to rebuild it using uv:
    powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1 -RecreateVenv

  Tip: bind 127.0.0.1 (default) for local-only use. Use 0.0.0.0 only when you
  intentionally want other devices on your LAN to reach it.
#>

param(
    [int]$Port = 7000,
    [string]$BindHost = "127.0.0.1",

    # Use a stable Python by default. You can override, e.g. -PythonVersion 3.11 or 3.13
    [string]$PythonVersion = "3.12",

    # Deletes and recreates venv. Useful when switching from system Python to uv-managed Python.
    [switch]$RecreateVenv
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Step($msg) {
    Write-Host ""
    Write-Host ("==> " + $msg) -ForegroundColor Cyan
}

function Fail($msg) {
    Write-Host ""
    Write-Host ("ERROR: " + $msg) -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

function Run-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,

        [Parameter(Mandatory = $true)]
        [string]$FailureMessage
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        Fail $FailureMessage
    }
}

function Find-GitBash {
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

function Get-UvWindowsTriple {
    $arch = $env:PROCESSOR_ARCHITECTURE

    if ($env:PROCESSOR_ARCHITEW6432) {
        $arch = $env:PROCESSOR_ARCHITEW6432
    }

    switch ($arch) {
        "AMD64" { return "x86_64-pc-windows-msvc" }
        "ARM64" { return "aarch64-pc-windows-msvc" }
        "x86"   { return "i686-pc-windows-msvc" }
        default {
            Fail "Unsupported Windows CPU architecture: $arch"
        }
    }
}

function Ensure-LocalUv {
    param(
        [Parameter(Mandatory = $true)]
        [string]$UvExe
    )

    if (Test-Path $UvExe) {
        return $UvExe
    }

    Write-Step "Installing uv locally"

    $uvBinDir = Split-Path -Parent $UvExe
    $uvRootDir = Split-Path -Parent $uvBinDir
    $extractDir = Join-Path $uvRootDir "extract"
    $zipPath = Join-Path $uvRootDir "uv.zip"

    New-Item -ItemType Directory -Force -Path $uvBinDir | Out-Null

    $triple = Get-UvWindowsTriple
    $url = "https://github.com/astral-sh/uv/releases/latest/download/uv-$triple.zip"

    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    } catch {
        # Continue; older Windows/PowerShell installations may already have a usable default.
    }

    try {
        if (Test-Path $extractDir) {
            Remove-Item $extractDir -Recurse -Force
        }

        New-Item -ItemType Directory -Force -Path $extractDir | Out-Null

        Write-Host "Downloading uv..."
        Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing

        Write-Host "Extracting uv..."
        Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force

        $uvCandidate = Get-ChildItem -Path $extractDir -Recurse -Filter "uv.exe" |
            Select-Object -First 1

        if (-not $uvCandidate) {
            Fail "Downloaded uv archive did not contain uv.exe."
        }

        Copy-Item -Path $uvCandidate.FullName -Destination $UvExe -Force

        if (-not (Test-Path $UvExe)) {
            Fail "Failed to install uv locally."
        }
    } catch {
        Fail "Failed to download/install uv locally. Details: $($_.Exception.Message)"
    } finally {
        if (Test-Path $zipPath) {
            Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
        }

        if (Test-Path $extractDir) {
            Remove-Item $extractDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    return $UvExe
}

# Project-local uv and Python locations.
$uvRoot = Join-Path $PSScriptRoot ".uv"
$uvBin = Join-Path $uvRoot "bin"
$uvExe = Join-Path $uvBin "uv.exe"

# Keep uv-managed Python and uv cache local to this project instead of system-wide.
$env:UV_PYTHON_INSTALL_DIR = Join-Path $uvRoot "python"
$env:UV_CACHE_DIR = Join-Path $uvRoot "cache"

$venvDir = Join-Path $PSScriptRoot "venv"
$venvPy = Join-Path $venvDir "Scripts\python.exe"

# 1. Ensure uv exists locally.
$uvExe = Ensure-LocalUv -UvExe $uvExe
Write-Host "Using uv: $uvExe"

# 2. Install a uv-managed Python locally.
Write-Step "Installing uv-managed Python $PythonVersion locally"
Run-Checked `
    -FilePath $uvExe `
    -Arguments @("python", "install", $PythonVersion) `
    -FailureMessage "Failed to install uv-managed Python $PythonVersion."

# 3. Create or recreate the virtual environment using uv-managed Python only.
if ($RecreateVenv -and (Test-Path $venvDir)) {
    Write-Step "Removing existing virtual environment"
    Remove-Item $venvDir -Recurse -Force
}

if (-not (Test-Path $venvPy)) {
    Write-Step "Creating virtual environment with uv-managed Python"
    Run-Checked `
        -FilePath $uvExe `
        -Arguments @(
            "venv",
            $venvDir,
            "--python",
            $PythonVersion,
            "--python-preference",
            "only-managed"
        ) `
        -FailureMessage "Failed to create the virtual environment."

    if (-not (Test-Path $venvPy)) {
        Fail "Virtual environment was created, but venv\Scripts\python.exe was not found."
    }
} else {
    Write-Host "venv already exists - skipping creation."
    Write-Host "Tip: use -RecreateVenv if this venv was previously created with system Python."
}

# 4. Install / update dependencies with uv.
Write-Step "Installing dependencies with uv"

Run-Checked `
    -FilePath $uvExe `
    -Arguments @(
        "pip",
        "install",
        "--python",
        $venvPy,
        "-r",
        "requirements.txt"
    ) `
    -FailureMessage "Dependency install failed. Scroll up for the uv/pip error."

# 5. First-time setup creates data dirs, DB, .env, admin user.
Write-Step "Running first-time setup"

Run-Checked `
    -FilePath $venvPy `
    -Arguments @("setup.py") `
    -FailureMessage "setup.py failed."

# 6. Friendly note about Git Bash.
if (-not (Find-GitBash)) {
    Write-Host ""
    Write-Host "NOTE: Git Bash (bash.exe) was not found on PATH." -ForegroundColor Yellow
    Write-Host "      The core app works without it. For full Cookbook background" -ForegroundColor Yellow
    Write-Host "      downloads and the agent shell tool, install Git for Windows:" -ForegroundColor Yellow
    Write-Host "      https://git-scm.com/download/win" -ForegroundColor Yellow
}

# 7. Start the server.
Write-Step ("Starting Odysseus at http://{0}:{1}" -f $BindHost, $Port)
Write-Host "Press Ctrl+C to stop."
Write-Host ""

Run-Checked `
    -FilePath $venvPy `
    -Arguments @(
        "-m",
        "uvicorn",
        "app:app",
        "--host",
        $BindHost,
        "--port",
        "$Port"
    ) `
    -FailureMessage "uvicorn exited with an error."
