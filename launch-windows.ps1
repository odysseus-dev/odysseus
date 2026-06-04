#Requires -Version 5.1
<#
  launch-windows.ps1 - DEPRECATED direct entry point.

  Use .\odysseus.ps1 --launch=native instead. The same code path runs and
  you get the full flag surface for free:
    .\odysseus.ps1 --launch=native
    .\odysseus.ps1 --update
    .\odysseus.ps1 --port=7900

  When odysseus.ps1 calls back into this script for the real native launch
  logic, it sets $env:ODYSSEUS_LEGACY_ENTRY=1 to skip the deprecation
  banner below.
#>
[CmdletBinding()]
param(
    [int]$Port = 7000,
    [string]$BindHost = "127.0.0.1"
)

if ($env:ODYSSEUS_LEGACY_ENTRY -ne "1") {
    Write-Host "==> launch-windows.ps1 is deprecated - use .\odysseus.ps1 --launch=native" -ForegroundColor Yellow
    Write-Host "    (forwarding to odysseus.ps1; this shim will be removed in a future release)" -ForegroundColor Yellow
    Write-Host ""
    & (Join-Path $PSScriptRoot "odysseus.ps1") --launch=native --port=$Port --host=$BindHost
    return
}

# Re-entrant: odysseus.ps1 is delegating to us. Run the original native
# launcher logic from scripts/legacy/windows-native.ps1.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
& (Join-Path $PSScriptRoot "scripts\legacy\windows-native.ps1") -Port $Port -BindHost $BindHost