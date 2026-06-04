<#
  odysseus.ps1 - one launcher for the native Windows install path.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\odysseus.ps1
    powershell -ExecutionPolicy Bypass -File .\odysseus.ps1 --launch=docker
    powershell -ExecutionPolicy Bypass -File .\odysseus.ps1 --update
    powershell -ExecutionPolicy Bypass -File .\odysseus.ps1 --port=7900

  Flag surface is identical to odysseus.sh so the same docs apply to both.
  Old entry points (launch-windows.ps1, update_windows.bat) are now thin
  shims that call into this script.
#>
[CmdletBinding()]
param(
    [ValidateSet("native", "docker", "docker-nvidia", "docker-amd")]
    [string]$Launch = "native",
    [switch]$Update,
    [switch]$AddToPath,
    [switch]$RemoveFromPath,
    [switch]$InstallService,
    [switch]$UninstallService,
    [int]$Port = 0,
    [string]$Host = "",
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
$RepoDir = $PSScriptRoot
Set-Location -Path $RepoDir

# Apply env-var defaults for the bits the CLI flags didn't set, so .env and
# the CLI behave the same way.
if ($Port -eq 0) {
    if ($env:ODYSSEUS_PORT) { $Port = [int]$env:ODYSSEUS_PORT }
    elseif ($env:APP_PORT)  { $Port = [int]$env:APP_PORT }
    else                    { $Port = 7000 }
}
if ([string]::IsNullOrEmpty($Host)) {
    if ($env:ODYSSEUS_HOST) { $Host = $env:ODYSSEUS_HOST }
    elseif ($env:APP_BIND)  { $Host = $env:APP_BIND }
    else                    { $Host = "127.0.0.1" }
}

function Write-Step($msg) { Write-Host ""; Write-Host ("==> " + $msg) -ForegroundColor Cyan }

# ── --add-to-path / --remove-from-path ─────────────────────────────────────
# Mirrors odysseus.sh: drop a copy at ~/.local/bin (which Windows treats as
# $env:USERPROFILE\.local\bin) and prepend it to the user PATH for future
# shells. --remove-from-path reverses the change.
if ($AddToPath) {
    $binDir = Join-Path $env:USERPROFILE ".local\bin"
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null
    $exeTarget = Join-Path $binDir "odysseus.ps1"
    # Junction a copy of the script into bin so the symlink doesn't break if
    # the repo moves. Windows' mklink /J works for directories; for files we
    # use a hard-link-friendly approach via New-Item.
    Copy-Item -Path (Join-Path $RepoDir "odysseus.ps1") -Destination $exeTarget -Force

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -notlike "*$binDir*") {
        [Environment]::SetEnvironmentVariable("Path", "$binDir;$userPath", "User")
        $env:Path = "$binDir;$env:Path"
        Write-Host "  ✓ added $binDir to user PATH. Open a new PowerShell to pick it up."
    } else {
        Write-Host "  ✓ $binDir is already on the user PATH."
    }
    exit 0
}

if ($RemoveFromPath) {
    $binDir = Join-Path $env:USERPROFILE ".local\bin"
    $exeTarget = Join-Path $binDir "odysseus.ps1"
    if (Test-Path $exeTarget) { Remove-Item $exeTarget -Force }
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -like "*$binDir*") {
        $newPath = ($userPath -split ';' | Where-Object { $_ -ne $binDir }) -join ';'
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        $env:Path = ($env:Path -split ';' | Where-Object { $_ -ne $binDir }) -join ';'
        Write-Host "  ✓ removed $binDir from user PATH."
    }
    exit 0
}

# ── --update ───────────────────────────────────────────────────────────────
if ($Update) {
    Write-Step "git pull"
    git pull --rebase --autostash
    if ($LASTEXITCODE -ne 0) { throw "git pull failed." }
}

# ── --launch dispatch ──────────────────────────────────────────────────────
switch ($Launch) {
    "native" {
        # Delegate to the legacy Windows native launcher (the original
        # launch-windows.ps1 logic, now in scripts/legacy/ so the public
        # entry point can be a thin shim). ODYSSEUS_LEGACY_ENTRY=1 tells
        # that script to skip its own deprecation banner.
        $env:ODYSSEUS_LEGACY_ENTRY = "1"
        & (Join-Path $RepoDir "scripts\legacy\windows-native.ps1") -Port $Port -BindHost $Host
        return
    }
    "docker" {
        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
            throw "Docker isn't installed. Install Docker Desktop and re-run."
        }
        $composeFile = "docker-compose.yml"
        if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
            $nv = nvidia-smi -L 2>$null
            if ($LASTEXITCODE -eq 0 -and $nv) {
                $composeFile = "docker-compose.gpu-nvidia.yml"
                Write-Host "  ✓ detected NVIDIA GPU - using GPU overlay"
            }
        }
        if ($Update) { docker compose -f $composeFile build --pull }
        docker compose -f $composeFile up -d --build
        docker compose -f $composeFile logs -f odysseus
    }
    "docker-nvidia" {
        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw "Docker isn't installed." }
        if ($Update) { docker compose -f "docker-compose.gpu-nvidia.yml" build --pull }
        docker compose -f "docker-compose.gpu-nvidia.yml" up -d --build
        docker compose -f "docker-compose.gpu-nvidia.yml" logs -f odysseus
    }
    "docker-amd" {
        throw "AMD/ROCm Docker is not supported on Windows. Use --launch=native or --launch=docker-nvidia."
    }
    default {
        throw "Unknown --launch value: $Launch"
    }
}
