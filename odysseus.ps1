#Requires -Version 5.1
<#
  Unified Odysseus Windows launcher.

  Preferred usage:
    .\odysseus.ps1 run -Launch native -Host 127.0.0.1 -Port 7000
    .\odysseus.ps1 update -Launch docker
    .\odysseus.ps1 add-to-path
    .\odysseus.ps1 remove-from-path
    .\odysseus.ps1 add-autostart
    .\odysseus.ps1 remove-autostart

  Backward-compatible aliases are accepted via legacy flags.
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet("run", "update", "add-to-path", "remove-from-path", "add-autostart", "remove-autostart", "help")]
    [string]$Command = "run",

    [ValidateSet("native", "docker", "docker-nvidia", "docker-amd")]
    [string]$Launch = "native",

    [int]$Port = 7000,

    [Alias("Host")]
    [string]$BindHost = "127.0.0.1",

    [switch]$NoLaunch,

    # Legacy compatibility flags
    [switch]$Update,
    [switch]$AddToPath,
    [switch]$RemoveFromPath,
    [switch]$AddAutostart,
    [switch]$RemoveAutostart,
    [switch]$Help
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
    exit 1
}

function Show-Help {
    @"
Odysseus Windows launcher

Usage:
  .\odysseus.ps1 <command> [options]

Commands:
  run               Create/refresh env and run the app
  update            Pull latest changes and refresh runtime dependencies
  add-to-path       Add this repo path to User PATH
  remove-from-path  Remove this repo path from User PATH
  add-autostart     Register Windows Scheduled Task at user logon
  remove-autostart  Remove Windows Scheduled Task
  help              Show this help

Options (run/update):
    -Launch native|docker|docker-nvidia|docker-amd     Default: native
    -Host <host>                                       Default: 127.0.0.1
    -Port <port>                                       Default: 7000
    -NoLaunch                                          (update only) do not launch after update

Notes:
  - docker-amd is not supported on Windows (ROCm Docker needs /dev/kfd).
  - Backward-compatible flags are accepted: -Update, -AddToPath, -RemoveFromPath,
    -AddAutostart, -RemoveAutostart, -BindHost.
"@ | Write-Host
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

function Get-PythonVersionText($launcher, $launcherArgs) {
    try {
        return (& $launcher @launcherArgs -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null).Trim()
    } catch {
        return $null
    }
}

function Resolve-Python {
    Write-Step "Checking for Python"

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

    if (-not $pyExe) {
        Fail "Couldn't find Python 3.11+ for Windows setup. Install Python 3.11+ from https://www.python.org/downloads/, then re-run this script."
    }

    $pythonLabel = ("Using Python {0}: {1} {2}" -f $pyVersion, $pyExe, ($pyArgs -join ' ')).TrimEnd()
    Write-Host $pythonLabel

    return @{
        Exe = $pyExe
        Args = $pyArgs
    }
}

function Ensure-NativeRuntime {
    $py = Resolve-Python
    $venvPy = Join-Path $PSScriptRoot "venv\Scripts\python.exe"

    if (-not (Test-Path $venvPy)) {
        Write-Step "Creating virtual environment (venv)"
        & $py.Exe @($py.Args) -m venv venv
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) { Fail "Failed to create the virtual environment." }
    } else {
        Write-Host "venv already exists - skipping creation."
    }

    Write-Step "Installing dependencies"
    & $venvPy -m pip install --upgrade pip --quiet
    & $venvPy -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Fail "Dependency install failed. Scroll up for the pip error." }

    Write-Step "Running first-time setup"
    & $venvPy setup.py
    if ($LASTEXITCODE -ne 0) { Fail "setup.py failed." }

    if (-not (Find-GitBash)) {
        Write-Host ""
        Write-Host "NOTE: Git Bash (bash.exe) was not found on PATH." -ForegroundColor Yellow
        Write-Host "      Core app works without it. For full Cookbook/agent-shell parity:" -ForegroundColor Yellow
        Write-Host "      https://git-scm.com/download/win" -ForegroundColor Yellow
    }

    return $venvPy
}

function Resolve-DockerComposeFile {
    param([string]$RequestedLaunch)

    if ($RequestedLaunch -eq "docker-amd") {
        Fail "docker-amd is not supported on Windows. ROCm Docker requires Linux kernel devices (/dev/kfd). Use: .\odysseus.ps1 run -Launch native"
    }

    if ($RequestedLaunch -eq "docker-nvidia") {
        return "docker-compose.yml:docker/gpu.nvidia.yml"
    }

    if ($RequestedLaunch -eq "docker") {
        $hasNvidia = $false
        $nvidiaCmd = Get-Command nvidia-smi -ErrorAction SilentlyContinue
        if ($nvidiaCmd) {
            & $nvidiaCmd.Source 1>$null 2>$null
            if ($LASTEXITCODE -eq 0) { $hasNvidia = $true }
        }
        if ($hasNvidia) {
            Write-Host "NVIDIA GPU detected - using docker/gpu.nvidia.yml"
            return "docker-compose.yml:docker/gpu.nvidia.yml"
        }
        Write-Host "No NVIDIA Docker runtime detected - using CPU docker-compose.yml"
        return "docker-compose.yml"
    }

    return "docker-compose.yml"
}

function Invoke-Run {
    param([string]$RequestedLaunch, [string]$BindHost, [int]$BindPort)

    if ($RequestedLaunch -eq "native") {
        $venvPy = Ensure-NativeRuntime
        Write-Step ("Starting Odysseus at http://{0}:{1}" -f $BindHost, $BindPort)
        Write-Host "Press Ctrl+C to stop."
        Write-Host ""
        & $venvPy -m uvicorn app:app --host $BindHost --port $BindPort
        return
    }

    Write-Step "Starting Docker deployment"
    $composeFile = Resolve-DockerComposeFile -RequestedLaunch $RequestedLaunch
    $env:COMPOSE_FILE = $composeFile
    docker compose up -d
    if ($LASTEXITCODE -ne 0) { Fail "docker compose up failed." }
    Write-Host "Docker services started."
}

function Invoke-Update {
    param([string]$RequestedLaunch, [switch]$SkipLaunch)

    Write-Step "Pulling latest code"
    git pull --ff-only
    if ($LASTEXITCODE -ne 0) { Fail "git pull --ff-only failed. Resolve local branch state and retry." }

    $nativeVenvPy = $null
    if ($RequestedLaunch -eq "native") {
        $nativeVenvPy = Ensure-NativeRuntime
        Write-Step "Refreshing dependencies"
        & $nativeVenvPy -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) { Fail "Dependency refresh failed." }
    } else {
        Write-Step "Rebuilding Docker services"
        $composeFile = Resolve-DockerComposeFile -RequestedLaunch $RequestedLaunch
        $env:COMPOSE_FILE = $composeFile
        docker compose up -d --build
        if ($LASTEXITCODE -ne 0) { Fail "docker compose up -d --build failed." }

        Write-Step "Pruning dangling Docker images"
        docker image prune -f
        if ($LASTEXITCODE -ne 0) { Fail "docker image prune failed." }
    }

    if (-not $SkipLaunch) {
        if ($RequestedLaunch -eq "native") {
            Write-Step ("Starting Odysseus at http://{0}:{1}" -f $BindHost, $Port)
            Write-Host "Press Ctrl+C to stop."
            Write-Host ""
            & $nativeVenvPy -m uvicorn app:app --host $BindHost --port $Port
        } else {
            Invoke-Run -RequestedLaunch $RequestedLaunch -BindHost $BindHost -BindPort $Port
        }
    }
}

function Add-ToPath {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @()
    if ($userPath) { $parts = $userPath.Split(';') | Where-Object { $_ -and $_.Trim() } }

    foreach ($p in $parts) {
        if ($p.TrimEnd('\\').ToLowerInvariant() -eq $PSScriptRoot.TrimEnd('\\').ToLowerInvariant()) {
            Write-Host "Path already present in User PATH: $PSScriptRoot"
            return
        }
    }

    $newPath = if ($userPath) { "$userPath;$PSScriptRoot" } else { $PSScriptRoot }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Host "Added to User PATH: $PSScriptRoot"
    Write-Host "Open a new shell to pick up the updated PATH."
}

function Remove-FromPath {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $userPath) {
        Write-Host "User PATH is empty; nothing to remove."
        return
    }

    $parts = $userPath.Split(';') | Where-Object { $_ -and $_.Trim() }
    $filtered = @()
    foreach ($p in $parts) {
        if ($p.TrimEnd('\\').ToLowerInvariant() -ne $PSScriptRoot.TrimEnd('\\').ToLowerInvariant()) {
            $filtered += $p
        }
    }

    [Environment]::SetEnvironmentVariable("Path", ($filtered -join ';'), "User")
    Write-Host "Removed from User PATH (if present): $PSScriptRoot"
}

function Add-Autostart {
    $taskName = "OdysseusLauncher"
    $pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue)
    $shellExe = if ($pwsh) { $pwsh.Source } else { "powershell.exe" }

    $scriptPath = Join-Path $PSScriptRoot "odysseus.ps1"
    $argString = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$scriptPath`" run -Launch native -BindHost $BindHost -Port $Port"
    $taskRun = "`"$shellExe`" $argString"

    schtasks /Create /TN $taskName /SC ONLOGON /TR $taskRun /F | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "Failed to register scheduled task '$taskName'." }
    Write-Host "Autostart task registered: $taskName"
}

function Remove-Autostart {
    $taskName = "OdysseusLauncher"
    schtasks /Delete /TN $taskName /F | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Autostart task '$taskName' not found (or already removed)."
        return
    }
    Write-Host "Autostart task removed: $taskName"
}

# Map legacy flags to subcommands when explicitly requested
if ($Update) { $Command = "update" }
if ($AddToPath) { $Command = "add-to-path" }
if ($RemoveFromPath) { $Command = "remove-from-path" }
if ($AddAutostart) { $Command = "add-autostart" }
if ($RemoveAutostart) { $Command = "remove-autostart" }

if ($Help -or $Command -eq "help") {
    Show-Help
    exit 0
}

switch ($Command) {
    "run" {
        Invoke-Run -RequestedLaunch $Launch -BindHost $BindHost -BindPort $Port
    }
    "update" {
        Invoke-Update -RequestedLaunch $Launch -SkipLaunch:$NoLaunch
    }
    "add-to-path" {
        Add-ToPath
    }
    "remove-from-path" {
        Remove-FromPath
    }
    "add-autostart" {
        Add-Autostart
    }
    "remove-autostart" {
        Remove-Autostart
    }
    default {
        Show-Help
        exit 1
    }
}