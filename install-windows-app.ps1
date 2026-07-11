#Requires -Version 5.1
<#
  Install Odysseus like a Windows app.

  Copies the built Docker launcher into a real app folder and creates
  desktop / Start Menu shortcuts that point at the exe.

  Assumes you've already built:
    dist\Odysseus-Docker\Odysseus-Docker.exe

  Usage:
    powershell -ExecutionPolicy Bypass -File .\install-windows-app.ps1
#>

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Step($msg) { Write-Host ""; Write-Host ("==> " + $msg) -ForegroundColor Cyan }
function Fail($msg) {
    Write-Host ""
    Write-Host ("ERROR: " + $msg) -ForegroundColor Red
    exit 1
}

$buildSource = Join-Path $PSScriptRoot "dist\Odysseus-Docker"
if (-not (Test-Path (Join-Path $buildSource "Odysseus-Docker.exe"))) {
    Fail "Build output not found. Run build-windows-docker-exe.ps1 first."
}

$installRoot = Join-Path $env:LOCALAPPDATA "Programs\Odysseus"
$installExe = Join-Path $installRoot "Odysseus-Docker.exe"
$desktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "Odysseus.lnk"
$startMenuDir = Join-Path ([Environment]::GetFolderPath("Programs")) "Odysseus"
$startMenuShortcut = Join-Path $startMenuDir "Odysseus.lnk"

Write-Step "Installing app files"
New-Item -ItemType Directory -Force -Path $installRoot | Out-Null

$copyItems = @(
    "app.py",
    "launcher_docker.py",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.gpu-amd.yml",
    "docker-compose.gpu-nvidia.yml",
    "requirements.txt",
    "requirements-optional.txt",
    "pyproject.toml",
    ".env.example",
    ".env"
)

foreach ($item in $copyItems) {
    $src = Join-Path $PSScriptRoot $item
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination $installRoot -Force
    }
}

foreach ($dir in @("config", "core", "docker", "mcp_servers", "routes", "scripts", "services", "src", "static")) {
    $srcDir = Join-Path $PSScriptRoot $dir
    if (Test-Path $srcDir) {
        Copy-Item -Path $srcDir -Destination $installRoot -Recurse -Force
    }
}

Copy-Item -Path (Join-Path $buildSource "*") -Destination $installRoot -Recurse -Force

# Preserve the current installed login so rebuilds don't fall back to the old admin account.
$authSource = Join-Path $PSScriptRoot "data\auth.json"
$authDestDir = Join-Path $installRoot "data"
if (Test-Path $authSource) {
    New-Item -ItemType Directory -Force -Path $authDestDir | Out-Null
    Copy-Item -Path $authSource -Destination (Join-Path $authDestDir "auth.json") -Force
}

function New-Shortcut($path, $target, $iconPath) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $target
    $shortcut.WorkingDirectory = Split-Path $target
    $shortcut.IconLocation = "$iconPath,0"
    $shortcut.Save()
}

Write-Step "Creating shortcuts"
New-Item -ItemType Directory -Force -Path $startMenuDir | Out-Null
New-Shortcut -path $desktopShortcut -target $installExe -iconPath (Join-Path $installRoot "static\icon.ico")
New-Shortcut -path $startMenuShortcut -target $installExe -iconPath (Join-Path $installRoot "static\icon.ico")

Write-Host ""
Write-Host "Installed to: $installRoot" -ForegroundColor Green
Write-Host "Desktop shortcut: $desktopShortcut" -ForegroundColor Green
Write-Host "Start Menu shortcut: $startMenuShortcut" -ForegroundColor Green
