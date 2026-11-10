#Requires -Version 5.1
<#
  Build the Windows installer for Odysseus from the portable bundle.

  Output:
    dist\installer\Odysseus-Setup-<version>.exe
    dist\installer\Odysseus-Setup-<version>.exe.sha256

  Requires Inno Setup 6: https://jrsoftware.org/isdl.php

  Usage:
    powershell -ExecutionPolicy Bypass -File .\build-windows-installer.ps1
    powershell -ExecutionPolicy Bypass -File .\build-windows-installer.ps1 -SkipBuild
#>
param(
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Step($msg) { Write-Host ""; Write-Host ("==> " + $msg) -ForegroundColor Cyan }
function Fail($msg) {
    Write-Host ""
    Write-Host ("ERROR: " + $msg) -ForegroundColor Red
    exit 1
}

Write-Step "Preparing portable bundle"
$appExe = Join-Path $PSScriptRoot "dist\Odysseus\Odysseus.exe"
if ($SkipBuild -and (Test-Path $appExe)) {
    Write-Host "Reusing existing $appExe"
} else {
    powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "build-windows-portable.ps1")
    if ($LASTEXITCODE -ne 0) { Fail "Portable build failed." }
}
if (-not (Test-Path $appExe)) { Fail "Missing $appExe after portable build." }

Write-Step "Reading application version"
$constants = Get-Content -Raw -Encoding UTF8 (Join-Path $PSScriptRoot "src\constants.py")
$match = [regex]::Match($constants, '(?m)^APP_VERSION\s*=\s*"([^"]+)"')
if (-not $match.Success) { Fail "APP_VERSION not found in src\constants.py." }
$version = $match.Groups[1].Value
if ($version -notmatch '^\d+(\.\d+)*$') { Fail "Unexpected APP_VERSION value: $version" }
Write-Host ("Version: " + $version)

Write-Step "Locating Inno Setup compiler"
$iscc = $null
$cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if ($cmd) { $iscc = $cmd.Source }
if (-not $iscc) {
    $roots = @()
    if (${env:ProgramFiles(x86)}) { $roots += ${env:ProgramFiles(x86)} }
    if ($env:ProgramFiles) { $roots += $env:ProgramFiles }
    if ($env:LOCALAPPDATA) { $roots += (Join-Path $env:LOCALAPPDATA "Programs") }
    foreach ($root in $roots) {
        $candidate = Join-Path $root "Inno Setup 6\ISCC.exe"
        if (Test-Path $candidate) { $iscc = $candidate; break }
    }
}
if (-not $iscc) {
    Fail "Inno Setup 6 not found. Install it from https://jrsoftware.org/isdl.php and run this script again."
}
Write-Host ("Using ISCC: " + $iscc)

Write-Step "Compiling installer"
$outDir = Join-Path $PSScriptRoot "dist\installer"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$iss = Join-Path $PSScriptRoot "installer\odysseus.iss"
& $iscc "/DAppVersion=$version" $iss
if ($LASTEXITCODE -ne 0) { Fail "ISCC failed with exit code $LASTEXITCODE." }

$setupExe = Join-Path $outDir ("Odysseus-Setup-" + $version + ".exe")
if (-not (Test-Path $setupExe)) { Fail "Installer not produced: $setupExe" }
$hash = (Get-FileHash -Algorithm SHA256 -Path $setupExe).Hash.ToLower()
Set-Content -Path ($setupExe + ".sha256") -Value ($hash + "  " + (Split-Path $setupExe -Leaf)) -Encoding ascii

Write-Host ""
Write-Host "Installer build complete." -ForegroundColor Green
Write-Host ("Installer: " + $setupExe) -ForegroundColor Green
Write-Host ("SHA-256:   " + $hash) -ForegroundColor Green
