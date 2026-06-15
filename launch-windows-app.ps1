#Requires -Version 5.1
<#
.SYNOPSIS
    Open the Odysseus desktop launcher GUI.
.DESCRIPTION
    Mirrors build-macos-app.sh: a double-clickable desktop entry that shows
    preflight status, starts the server, and opens the UI in browser app mode.

    Prefer Odysseus.vbs or the Desktop shortcut from build-windows-launcher.ps1.

    Contributed by Wykeve (VoidCat) - 2026.
#>
$launcher = Join-Path $PSScriptRoot 'launcher\Odysseus.Launcher.ps1'
if (-not (Test-Path $launcher)) {
    Write-Error "Launcher not found: $launcher"
}
Start-Process -FilePath 'powershell.exe' -ArgumentList @(
    '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-File', $launcher
)