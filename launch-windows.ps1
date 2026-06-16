#Requires -Version 5.1
<#
  Compatibility wrapper for legacy Windows launcher entrypoint.

  This file is intentionally minimal and forwards to odysseus.ps1.
#>
param(
    [int]$Port = 7000,
    [string]$BindHost = "127.0.0.1",
    [switch]$Update
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "[DEPRECATED] launch-windows.ps1 is deprecated." -ForegroundColor Yellow
Write-Host "             Use: .\odysseus run --host <host> --port <port>" -ForegroundColor Yellow
Write-Host ""

$target = Join-Path $PSScriptRoot "odysseus.ps1"
if (-not (Test-Path $target)) {
    Write-Host "ERROR: odysseus.ps1 was not found next to launch-windows.ps1" -ForegroundColor Red
    exit 1
}

if ($Update) {
    & $target update -Launch native -Host $BindHost -Port $Port
} else {
    & $target run -Launch native -Host $BindHost -Port $Port
}

exit $LASTEXITCODE
