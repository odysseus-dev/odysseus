#Requires -Version 5.1
<#
  Odysseus - Windows Native App Wrapper Builder
  
  This script installs the pywebview dependency into the local environment
  and creates a Desktop shortcut pointing to the new desktop.py wrapper.
#>

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Step($msg) { Write-Host ""; Write-Host ("==> " + $msg) -ForegroundColor Cyan }
function Fail($msg) {
    Write-Host ""
    Write-Host ("ERROR: " + $msg) -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

$venvPy = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
$venvPyw = Join-Path $PSScriptRoot "venv\Scripts\pythonw.exe"

if (-not (Test-Path $venvPy)) {
    Write-Step "Odysseus virtual environment not found."
    Fail "Please run launch-windows.ps1 first to fully install Odysseus before building the app."
}

Write-Step "Installing PyWebView dependency into virtual environment..."
& $venvPy -m pip install pywebview
if ($LASTEXITCODE -ne 0) { Fail "Dependency install failed. Scroll up for the pip error." }

Write-Step "Creating Desktop Shortcut..."
$WshShell = New-Object -ComObject WScript.Shell
$DesktopPath = [System.Environment]::GetFolderPath('Desktop')
$ShortcutPath = Join-Path $DesktopPath "Odysseus.lnk"

# Remove existing shortcut if it exists
if (Test-Path $ShortcutPath) {
    Remove-Item $ShortcutPath -Force
}

$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $venvPyw
$Shortcut.Arguments = "desktop.py"
$Shortcut.WorkingDirectory = $PSScriptRoot
$Shortcut.WindowStyle = 1 # Normal window (the Python window won't show because of pythonw, but the webview window will)
$Shortcut.Description = "Odysseus Self-Hosted AI Workspace"

# Save the shortcut
$Shortcut.Save()

Write-Step "Done! A shortcut named 'Odysseus' has been placed on your Desktop."
Write-Host "You can now close this console and launch Odysseus exactly like a native app!" -ForegroundColor Green
