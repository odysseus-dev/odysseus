#Requires -Version 5.1
<#
  Build a single-click Windows executable that starts Docker Desktop,
  launches the Odysseus compose stack, waits for the app, and opens a
  standalone Edge app window when available.

  Output layout:
    dist\Odysseus-Docker\Odysseus-Docker.exe

  Usage:
    powershell -ExecutionPolicy Bypass -File .\build-windows-docker-exe.ps1
#>

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Step($msg) { Write-Host ""; Write-Host ("==> " + $msg) -ForegroundColor Cyan }
function Fail($msg) {
    Write-Host ""
    Write-Host ("ERROR: " + $msg) -ForegroundColor Red
    exit 1
}

Write-Step "Checking for Python"
$pyExe = $env:PYTHON_EXE
if ($pyExe -and (Test-Path $pyExe)) {
    $pyExe = (Resolve-Path $pyExe).Path
} elseif (Test-Path ".\.venv\Scripts\python.exe") {
    $pyExe = (Resolve-Path ".\.venv\Scripts\python.exe").Path
} else {
    foreach ($c in @("py", "python")) {
        $cmd = Get-Command $c -ErrorAction SilentlyContinue
        if ($cmd) { $pyExe = $cmd.Source; break }
    }
    if ($pyExe -like "*WindowsApps*python.exe") {
        $pyCmd = Get-Command py -ErrorAction SilentlyContinue
        if ($pyCmd) { $pyExe = $pyCmd.Source }
    }
}
if (-not $pyExe) {
    Fail "Python not found on PATH. Install Python 3.11+ first."
}
Write-Host ("Using Python: " + $pyExe)

Write-Step "Installing build dependencies"
& $pyExe -m pip install --upgrade pip --quiet
& $pyExe -m pip install pyinstaller pywebview pillow
if ($LASTEXITCODE -ne 0) { Fail "Dependency install failed." }

Write-Step "Building Docker launcher exe"
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
& $pyExe -m PyInstaller --noconfirm --clean --onedir --noconsole --icon static\icon.ico --name Odysseus-Docker launcher_docker.py
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller build failed." }

Write-Host ""
Write-Host "Build complete." -ForegroundColor Green
Write-Host "Executable: $PSScriptRoot\dist\Odysseus-Docker\Odysseus-Docker.exe" -ForegroundColor Green
