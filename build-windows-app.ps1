#Requires -Version 5.1
<#
  Build a clickable Windows desktop app for Odysseus (the equivalent of
  build-macos-app.sh).

    powershell -ExecutionPolicy Bypass -File .\build-windows-app.ps1

  Produces a native-window app: a small Python launcher starts the local
  server (using this repo's venv, no terminal window) and shows the UI in a
  WebView2 window -- the same Chromium engine the browser uses, so the login
  animation and everything else render identically. The window and a taskbar
  pin carry the Odysseus logo, via an explicit AppUserModelID.

  Produces, under dist\:
    odysseus.ico            - app icon, drawn from the Odysseus brand mark.
    Odysseus-launcher.py    - the launcher (server + WebView2 window).
    Odysseus.lnk            - double-click app shortcut (pythonw, no console).

  By default it also installs a matching Start-Menu shortcut so the app shows
  up in search and pins to the taskbar with the correct icon (pass -NoStartMenu
  to skip). Override the port with -Port. Like the macOS builder this drives the
  repo venv rather than bundling Python, so re-run it if you move the repo.

  Requirements: run launch-windows.ps1 once first (creates the venv). This
  script installs pywebview into that venv; WebView2 ships with Windows 11.
#>
param(
    [int]$Port = $(if ($env:ODYSSEUS_PORT) { [int]$env:ODYSSEUS_PORT } else { 7000 }),
    [switch]$NoStartMenu
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$AppName    = "Odysseus"
$AppId      = "Odysseus.App"
$InstallDir = $PSScriptRoot
$Dist       = Join-Path $InstallDir "dist"
$Launcher   = Join-Path $Dist "$AppName-launcher.py"
$Lnk        = Join-Path $Dist "$AppName.lnk"
$IcoPath    = Join-Path $Dist "odysseus.ico"
$VenvPy     = Join-Path $InstallDir "venv\Scripts\python.exe"
$VenvPyw    = Join-Path $InstallDir "venv\Scripts\pythonw.exe"

function Fail($msg) { Write-Host ""; Write-Host ("ERROR: " + $msg) -ForegroundColor Red; exit 1 }

Write-Host "Building $AppName desktop app"
Write-Host ("  install dir: " + $InstallDir)
Write-Host ("  port:        " + $Port)

if (-not (Test-Path $VenvPy)) {
    Fail "venv not found. Run this first:`n  powershell -ExecutionPolicy Bypass -File .\launch-windows.ps1"
}
New-Item -ItemType Directory -Force -Path $Dist | Out-Null

# -- Icon: draw the Odysseus brand mark (coral sail + wave) at several sizes
#    and pack them into a single .ico. The mark mirrors the inline-SVG favicon
#    in static/index.html so the app icon matches the in-app brand. --
Add-Type -AssemblyName System.Drawing
function New-MarkPng([int]$S) {
    $k = $S / 32.0   # the mark is authored on a 0..32 viewBox
    $bmp = New-Object System.Drawing.Bitmap $S, $S
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.Clear([System.Drawing.Color]::Transparent)
    $coral  = [System.Drawing.Color]::FromArgb(255, 224, 108, 117)
    $coralL = [System.Drawing.Color]::FromArgb(153, 224, 108, 117)
    $bC = New-Object System.Drawing.SolidBrush $coral
    $bL = New-Object System.Drawing.SolidBrush $coralL
    $p1 = @((16,4),(16,22),(6,22))  | ForEach-Object { New-Object System.Drawing.PointF (([single]($_[0]*$k)),([single]($_[1]*$k))) }
    $g.FillPolygon($bC, [System.Drawing.PointF[]]$p1)
    $p2 = @((16,8),(16,22),(24,22)) | ForEach-Object { New-Object System.Drawing.PointF (([single]($_[0]*$k)),([single]($_[1]*$k))) }
    $g.FillPolygon($bL, [System.Drawing.PointF[]]$p2)
    $pen = New-Object System.Drawing.Pen $coral, ([single](2.5 * $k))
    $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $pen.EndCap   = [System.Drawing.Drawing2D.LineCap]::Round
    $PF = { param($x, $y) New-Object System.Drawing.PointF (([single]($x*$k)),([single]($y*$k))) }
    $g.DrawBezier($pen, (& $PF 4 24),  (& $PF 8 21.33),  (& $PF 12 21.33), (& $PF 16 24))
    $g.DrawBezier($pen, (& $PF 16 24), (& $PF 20 26.67), (& $PF 24 26.67), (& $PF 28 24))
    $g.Dispose()
    $ms = New-Object System.IO.MemoryStream
    $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
    , $ms.ToArray()
}
$sizes = @(16, 32, 48, 256)
$pngs = $sizes | ForEach-Object { , (New-MarkPng $_) }
$fs = [System.IO.File]::Create($IcoPath); $bw = New-Object System.IO.BinaryWriter $fs
$bw.Write([UInt16]0); $bw.Write([UInt16]1); $bw.Write([UInt16]$sizes.Count)
$offset = 6 + 16 * $sizes.Count
for ($i = 0; $i -lt $sizes.Count; $i++) {
    $s = $sizes[$i]; $len = $pngs[$i].Length; $dim = $(if ($s -ge 256) { 0 } else { $s })
    $bw.Write([Byte]$dim); $bw.Write([Byte]$dim); $bw.Write([Byte]0); $bw.Write([Byte]0)
    $bw.Write([UInt16]1); $bw.Write([UInt16]32); $bw.Write([UInt32]$len); $bw.Write([UInt32]$offset)
    $offset += $len
}
foreach ($p in $pngs) { $bw.Write($p) }
$bw.Flush(); $bw.Dispose(); $fs.Dispose()
Write-Host "  icon:        odysseus.ico (sizes: $($sizes -join ', '))"

# -- Ensure the WebView2 host (pywebview) is installed in the venv --
Write-Host "  deps:        installing pywebview (WebView2 host)..."
& $VenvPy -m pip install --quiet --upgrade pywebview pythonnet
if ($LASTEXITCODE -ne 0) { Fail "Failed to install pywebview into the venv." }

# -- Launcher (placeholders filled below) --
$launcherTmpl = @'
"""Odysseus desktop launcher (WebView2). Generated by build-windows-app.ps1."""
import os
import time
import ctypes
import subprocess
import urllib.request
import urllib.error

INSTALL_DIR = r"__INSTALL_DIR__"
PORT = __PORT__
ICON = r"__ICON__"
APP_ID = "__APP_ID__"
URL = "http://127.0.0.1:%d" % PORT
CREATE_NO_WINDOW = 0x08000000

# Stable taskbar identity so the window -- and a taskbar pin -- show the
# Odysseus icon instead of python's.
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
except Exception:
    pass


def _err(msg):
    try:
        ctypes.windll.user32.MessageBoxW(0, msg, "Odysseus", 0x10)
    except Exception:
        pass


def is_up():
    try:
        urllib.request.urlopen(URL, timeout=2)
        return True
    except urllib.error.HTTPError:
        return True  # server answered (login redirect / 401) = up
    except Exception:
        return False


venv_py = os.path.join(INSTALL_DIR, "venv", "Scripts", "python.exe")
log_path = os.path.join(INSTALL_DIR, "logs", "odysseus-app.log")
os.makedirs(os.path.dirname(log_path), exist_ok=True)

server = None
if not is_up():
    log = open(log_path, "ab")
    server = subprocess.Popen(
        [venv_py, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=INSTALL_DIR, stdout=log, stderr=log, creationflags=CREATE_NO_WINDOW,
    )

# Wait for readiness (first run downloads an embedding model -- allow ~3 min).
for _ in range(180):
    if is_up():
        break
    time.sleep(1)

try:
    import webview
except Exception:
    _err("pywebview is not installed in the venv.\n\nRe-run:\n  build-windows-app.ps1")
    raise

storage = os.path.join(INSTALL_DIR, "data", ".webview")
os.makedirs(storage, exist_ok=True)

webview.create_window("Odysseus", URL, width=1280, height=860)
# private_mode=False + storage_path persists login + theme across launches.
webview.start(icon=ICON, private_mode=False, storage_path=storage)

# Window closed -- stop the server we started (kill its process tree).
if server is not None:
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(server.pid)],
                       creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass
'@

# Literal .Replace() (not -replace): the paths go into Python raw strings
# (r"..."), so backslashes stay as-is and there's no regex/$ interpretation.
$launcherText = $launcherTmpl.Replace('__INSTALL_DIR__', $InstallDir).Replace('__PORT__', "$Port").Replace('__ICON__', $IcoPath).Replace('__APP_ID__', $AppId)
Set-Content -Path $Launcher -Value $launcherText -Encoding UTF8
Write-Host "  launcher:    Odysseus-launcher.py"

# -- Shortcut (.lnk): pythonw (no console) + the launcher, with the icon --
$wsh = New-Object -ComObject WScript.Shell
$sc = $wsh.CreateShortcut($Lnk)
$sc.TargetPath       = $VenvPyw
$sc.Arguments        = '"' + $Launcher + '"'
$sc.WorkingDirectory = $InstallDir
$sc.IconLocation     = "$IcoPath,0"
$sc.Description       = "Odysseus - local AI workspace"
$sc.Save()

# -- Stamp the shortcut's AppUserModelID (IPropertyStore) so a taskbar pin
#    resolves to this shortcut -- and shows the Odysseus icon -- instead of
#    falling back to python.exe. --
if (-not ("Odysseus.ShortcutAumid" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
namespace Odysseus {
  [StructLayout(LayoutKind.Sequential)] public struct PROPERTYKEY { public Guid fmtid; public uint pid; }
  [StructLayout(LayoutKind.Sequential)] public struct PROPVARIANT { public ushort vt; public ushort r1, r2, r3; public IntPtr p; public IntPtr p2; }
  [ComImport, Guid("0000010b-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IPersistFile {
    void GetClassID(out Guid id);
    [PreserveSig] int IsDirty();
    void Load([MarshalAs(UnmanagedType.LPWStr)] string f, int mode);
    void Save([MarshalAs(UnmanagedType.LPWStr)] string f, [MarshalAs(UnmanagedType.Bool)] bool remember);
    void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string f);
    void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string f);
  }
  [ComImport, Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IPropertyStore {
    void GetCount(out uint c);
    void GetAt(uint i, out PROPERTYKEY k);
    void GetValue(ref PROPERTYKEY k, out PROPVARIANT v);
    void SetValue(ref PROPERTYKEY k, ref PROPVARIANT v);
    void Commit();
  }
  [ComImport, Guid("00021401-0000-0000-C000-000000000046")] class CShellLink { }
  public static class ShortcutAumid {
    public static void Set(string lnk, string appId) {
      var link = new CShellLink();
      ((IPersistFile)link).Load(lnk, 2); // STGM_READWRITE
      var store = (IPropertyStore)link;
      var key = new PROPERTYKEY { fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };
      var pv = new PROPVARIANT { vt = 31 /* VT_LPWSTR */, p = Marshal.StringToCoTaskMemUni(appId) };
      store.SetValue(ref key, ref pv);
      store.Commit();
      Marshal.FreeCoTaskMem(pv.p);
      ((IPersistFile)link).Save(lnk, true);
    }
  }
}
"@
}
[Odysseus.ShortcutAumid]::Set($Lnk, $AppId)
Write-Host "  shortcut:    Odysseus.lnk (AppUserModelID=$AppId)"

# -- Install a matching Start-Menu shortcut (so pinning resolves correctly) --
if (-not $NoStartMenu) {
    $startMenu = Join-Path $env:AppData "Microsoft\Windows\Start Menu\Programs"
    $smLnk = Join-Path $startMenu "$AppName.lnk"
    Copy-Item -Path $Lnk -Destination $smLnk -Force   # copy preserves the AppUserModelID property
    Write-Host ("  start menu:  " + $smLnk)
}

Write-Host ""
Write-Host "Done:"
Write-Host ("  " + $Lnk)
Write-Host ""
Write-Host "Run it:        double-click '$Lnk'  (or launch 'Odysseus' from the Start Menu)"
Write-Host "Pin it:        right-click the running window or Start-Menu entry -> Pin to taskbar"
Write-Host "               (the pin keeps the Odysseus icon)"
