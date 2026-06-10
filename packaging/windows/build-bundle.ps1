#Requires -Version 5.1
param(
    [string]$Version = "0.1.0",
    [string]$MsiPath = "",
    [switch]$AcceptWixEula
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$DistDir = Join-Path $RepoRoot "dist\windows"
if (-not $MsiPath) { $MsiPath = Join-Path $DistDir "Odysseus.msi" }
$BundleOut = Join-Path $DistDir "OdysseusSetup.exe"
$DockerPrereqCmd = Join-Path $PSScriptRoot "prereq\DockerPrereq.cmd"
$DockerPrereqScript = Join-Path $PSScriptRoot "prereq\DockerPrereq.ps1"

function Fail($Message) {
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

$localWix = Join-Path $RepoRoot "build\tools\wix.exe"
if (Test-Path $localWix) {
    $wix = $localWix
} else {
    $wixCommand = Get-Command wix -ErrorAction SilentlyContinue
    if (-not $wixCommand) {
        Fail "WiX Toolset CLI was not found. Install it with: dotnet tool install --tool-path .\build\tools wix"
    }
    $wix = $wixCommand.Source
}

if (-not (Test-Path $MsiPath)) {
    Write-Host "MSI not found. Building MSI first."
    $msiArgs = @("-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "build-msi.ps1"), "-Version", $Version)
    if ($AcceptWixEula) { $msiArgs += "-AcceptWixEula" }
    & powershell @msiArgs
    if ($LASTEXITCODE -ne 0) { Fail "MSI build failed." }
}

New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

$wixArgs = @("build")
if ($AcceptWixEula) { $wixArgs += @("-acceptEula", "wix7") }
$wixArgs += @(
    (Join-Path $PSScriptRoot "Bundle.wxs"),
    "-ext", "WixToolset.BootstrapperApplications.wixext",
    "-d", "ProductVersion=$Version",
    "-d", "MsiPath=$MsiPath",
    "-d", "DockerPrereqCmd=$DockerPrereqCmd",
    "-d", "DockerPrereqScript=$DockerPrereqScript",
    "-o", $BundleOut
)

& $wix @wixArgs

if ($LASTEXITCODE -ne 0) {
    Fail "WiX bundle build failed. If the bootstrapper extension is missing, run: wix extension add WixToolset.BootstrapperApplications.wixext"
}

Write-Host "Built $BundleOut" -ForegroundColor Green
