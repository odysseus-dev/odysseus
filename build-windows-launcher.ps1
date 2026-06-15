#Requires -Version 5.1
<#
.SYNOPSIS
    Install a desktop shortcut for the Odysseus GUI launcher.
.DESCRIPTION
    Creates Odysseus.lnk on the current user's Desktop pointing at Odysseus.vbs.
    Optionally generates launcher/assets/odysseus.ico from docs/odysseus.jpg.

    Windows desktop launcher contributed by Wykeve (VoidCat) - 2026.
#>
param(
    [switch]$DesktopOnly
)

$ErrorActionPreference = 'Stop'
$RepoRoot = $PSScriptRoot
$VbsPath = Join-Path $RepoRoot 'Odysseus.vbs'
$AssetsDir = Join-Path $RepoRoot 'launcher\assets'
$IconPath = Join-Path $AssetsDir 'odysseus.ico'
$ImagePath = Join-Path $RepoRoot 'docs\odysseus.jpg'

if (-not (Test-Path $VbsPath)) {
    Write-Error "Missing entry point: $VbsPath"
}

if (-not (Test-Path $AssetsDir)) {
    New-Item -ItemType Directory -Path $AssetsDir -Force | Out-Null
}

if ((Test-Path $ImagePath) -and -not (Test-Path $IconPath)) {
    try {
        Add-Type -AssemblyName System.Drawing
        $img = [System.Drawing.Image]::FromFile($ImagePath)
        $bmp = New-Object System.Drawing.Bitmap 64, 64
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.DrawImage($img, 0, 0, 64, 64)
        $g.Dispose()
        $icon = [System.Drawing.Icon]::FromHandle($bmp.GetHicon())
        $fs = [System.IO.File]::Open($IconPath, [System.IO.FileMode]::Create)
        $icon.Save($fs)
        $fs.Close()
        $bmp.Dispose()
        $img.Dispose()
        Write-Host "Icon written: $IconPath"
    } catch {
        Write-Warning "Could not generate icon: $($_.Exception.Message)"
    }
}

$Desktop = [Environment]::GetFolderPath('Desktop')
$ShortcutPath = Join-Path $Desktop 'Odysseus.lnk'

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = (Join-Path $env:WINDIR 'System32\wscript.exe')
$Shortcut.Arguments = "`"$VbsPath`""
$Shortcut.WorkingDirectory = $RepoRoot
$Shortcut.Description = 'Odysseus - local AI workspace (VoidCat launcher)'
if (Test-Path $IconPath) {
    $Shortcut.IconLocation = $IconPath
}
$Shortcut.Save()

Write-Host ""
Write-Host "Desktop shortcut created:"
Write-Host "  $ShortcutPath"
Write-Host ""
Write-Host "Double-click Odysseus on your Desktop to open the launcher."
Write-Host "Logs: $RepoRoot\logs\launcher.log"
Write-Host "      $RepoRoot\logs\odysseus-server.log"