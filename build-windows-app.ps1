#Requires -Version 5.1
<#
  Odysseus — Build native Windows desktop app (launcher & control panel).
  Mirrors build-macos-app.sh.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\build-windows-app.ps1
    powershell -ExecutionPolicy Bypass -File .\build-windows-app.ps1 -NoShortcut

  Produces:
    dist\Odysseus.exe  — double-click: starts local server, provides control panel & tray icon
    Desktop shortcut   — creates "Odysseus.lnk" on Desktop (unless -NoShortcut passed)
#>
param(
    [switch]$NoShortcut
)

$ErrorActionPreference = "Stop"
$RepoDir = $PSScriptRoot
Set-Location -Path $RepoDir

$AppName = "Odysseus"
$DistDir = Join-Path $RepoDir "dist"
$WindowsDir = Join-Path $RepoDir "windows"
$SourceFile = Join-Path $WindowsDir "OdysseusLauncher.cs"
$IconFile = Join-Path $WindowsDir "odysseus.ico"
$BrandingPng = Join-Path $RepoDir "static\icons\icon-512.png"
$BrandingJpg = Join-Path $RepoDir "assets\branding\odysseus.jpg"
$OutExe = Join-Path $DistDir "$AppName.exe"

Write-Host "Building $AppName.exe for Windows" -ForegroundColor Cyan
Write-Host "  Repo directory: $RepoDir"
Write-Host "  Output target:  $OutExe"

# 1. Ensure dist folder
if (-not (Test-Path $DistDir)) {
    New-Item -ItemType Directory -Path $DistDir -Force | Out-Null
}

# 2. Build icon if missing
if (-not (Test-Path $IconFile)) {
    $pyCmd = Join-Path $RepoDir "venv\Scripts\python.exe"
    if (-not (Test-Path $pyCmd)) { $pyCmd = "python" }

    if (Test-Path $BrandingPng) {
        Write-Host "  Generating $IconFile from vector icon asset..."
        try {
            & $pyCmd -c @"
from PIL import Image
img = Image.open(r'$BrandingPng')
img.save(r'$IconFile', format='ICO', sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
"@ 2>$null
        } catch { }
    } elseif (Test-Path $BrandingJpg) {
        Write-Host "  Generating $IconFile from branding asset..."
        try {
            & $pyCmd -c @"
from PIL import Image
img = Image.open(r'$BrandingJpg')
w, h = img.size
min_dim = min(w, h)
left = (w - min_dim) // 2
top = (h - min_dim) // 2
img = img.crop((left, top, left + min_dim, top + min_dim))
img.save(r'$IconFile', format='ICO', sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
"@ 2>$null
        } catch { }
    }
}

if (-not (Test-Path $IconFile) -and (Test-Path (Join-Path $RepoDir "odysseus.ico"))) {
    Copy-Item (Join-Path $RepoDir "odysseus.ico") $IconFile -Force
}

# 3. Locate built-in C# compiler (csc.exe)
$cscCandidates = @(
    "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
    "C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)

$cscExe = $null
foreach ($cand in $cscCandidates) {
    if (Test-Path $cand) {
        $cscExe = $cand
        break
    }
}

if (-not $cscExe) {
    Write-Error "Microsoft .NET Framework C# compiler (csc.exe) not found."
    exit 1
}

Write-Host "  Using compiler: $cscExe"

# 4. Stop any existing running instance before compiling/replacing binary
Get-Process -Name $AppName -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500

# 5. Compile executable
$iconFlag = ""
if (Test-Path $IconFile) {
    $iconFlag = "/win32icon:`"$IconFile`""
}

$compileArgs = @(
    "/target:winexe",
    "/out:`"$OutExe`"",
    "/r:System.dll,System.Windows.Forms.dll,System.Drawing.dll,System.Net.dll",
    "/optimize+"
)
if ($iconFlag) { $compileArgs += $iconFlag }
$compileArgs += "`"$SourceFile`""

Write-Host "  Compiling executable..."
$proc = Start-Process -FilePath $cscExe -ArgumentList ($compileArgs -join " ") -Wait -PassThru -NoNewWindow
if ($proc.ExitCode -ne 0) {
    Write-Error "Compilation failed with exit code $($proc.ExitCode)."
    exit 1
}

# Also copy to repo root for convenient direct access
try {
    Copy-Item $OutExe (Join-Path $RepoDir "$AppName.exe") -Force
} catch {
    Write-Warning "Could not copy to root $AppName.exe: $_"
}

Write-Host "  Compiled: $OutExe" -ForegroundColor Green

# 5. Create Desktop shortcut
if (-not $NoShortcut) {
    try {
        $desktop = [Environment]::GetFolderPath("Desktop")
        $shortcutPath = Join-Path $desktop "$AppName.lnk"
        $wsh = New-Object -ComObject WScript.Shell
        $shortcut = $wsh.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = Join-Path $RepoDir "$AppName.exe"
        $shortcut.WorkingDirectory = $RepoDir
        if (Test-Path $IconFile) {
            $shortcut.IconLocation = $IconFile
        }
        $shortcut.Description = "Odysseus AI Workspace"
        $shortcut.Save()
        Write-Host "  Created Desktop shortcut: $shortcutPath" -ForegroundColor Green
    } catch {
        Write-Warning "Could not create Desktop shortcut: $_"
    }
}

Write-Host ""
Write-Host "Done:" -ForegroundColor Cyan
Write-Host "  $OutExe"
Write-Host "  $(Join-Path $RepoDir "$AppName.exe")"
Write-Host ""
Write-Host "Run it: double-click '$AppName.exe' or your Desktop shortcut."
