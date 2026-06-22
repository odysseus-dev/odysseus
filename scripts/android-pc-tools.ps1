#Requires -Version 5.1
<#
  Connect Android Odysseus to the full PC backend tool stack over ADB reverse.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\scripts\android-pc-tools.ps1
    powershell -ExecutionPolicy Bypass -File .\scripts\android-pc-tools.ps1 -Port 7000 -SetPcMode -LaunchApp

  The phone should use http://127.0.0.1:$Port inside the Android app. ADB reverse
  maps that phone-local URL back to this PC's Odysseus backend.
#>
param(
    [int]$Port = 7000,
    [string]$Device = "",
    [string]$Adb = "",
    [switch]$SetPcMode,
    [switch]$LaunchApp
)

$ErrorActionPreference = "Stop"
$PackageName = "com.odysseus.simplesignal"

function Find-Adb {
    param([string]$Explicit)
    if ($Explicit) {
        if (Test-Path -LiteralPath $Explicit) { return (Resolve-Path -LiteralPath $Explicit).Path }
        throw "ADB not found at: $Explicit"
    }
    $cmd = Get-Command adb -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $sdkAdb = Join-Path $env:LOCALAPPDATA "Android\Sdk\platform-tools\adb.exe"
    if (Test-Path -LiteralPath $sdkAdb) { return $sdkAdb }
    throw "adb.exe was not found. Install Android platform-tools or pass -Adb C:\path\to\adb.exe."
}

function Get-AdbDevice {
    param([string]$AdbPath, [string]$Requested)
    $lines = & $AdbPath devices | Where-Object { $_ -match "^\S+\s+device\b" }
    $ids = @($lines | ForEach-Object { ($_ -split "\s+")[0] })
    if ($Requested) {
        if ($ids -notcontains $Requested) {
            throw "Requested device '$Requested' is not connected. Connected devices: $($ids -join ', ')"
        }
        return $Requested
    }
    if ($ids.Count -eq 0) {
        throw "No authorized ADB device found. In Android Wireless debugging, pair/connect the phone first, then rerun this script."
    }
    if ($ids.Count -gt 1) {
        throw "Multiple ADB devices found: $($ids -join ', '). Rerun with -Device <serial>."
    }
    return $ids[0]
}

function Test-Backend {
    param([int]$Port)
    try {
        $res = Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:{0}/api/health" -f $Port) -TimeoutSec 2
        return ($res.StatusCode -ge 200 -and $res.StatusCode -lt 500)
    } catch {
        return $false
    }
}

function Set-AndroidPcMode {
    param([string]$AdbPath, [string]$Serial, [int]$Port)
    $url = "http://127.0.0.1:$Port"
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("odysseus_android_{0}.xml" -f ([Guid]::NewGuid().ToString("N")))
    $xml = @"
<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <string name="server_url">$url</string>
    <string name="app_mode">remote</string>
</map>
"@
    Set-Content -LiteralPath $tmp -Value $xml -Encoding UTF8
    try {
        & $AdbPath -s $Serial push $tmp /data/local/tmp/odysseus_android.xml | Out-Host
        & $AdbPath -s $Serial shell "run-as $PackageName sh -c 'mkdir -p shared_prefs && cp /data/local/tmp/odysseus_android.xml shared_prefs/odysseus_android.xml && chmod 600 shared_prefs/odysseus_android.xml'" | Out-Host
    } finally {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
}

$adbPath = Find-Adb $Adb
$serial = Get-AdbDevice $adbPath $Device

if (-not (Test-Backend $Port)) {
    Write-Warning ("No Odysseus backend answered at http://127.0.0.1:{0}/api/health. Start the PC backend first for tools to work." -f $Port)
}

& $adbPath -s $serial reverse ("tcp:{0}" -f $Port) ("tcp:{0}" -f $Port) | Out-Host
Write-Host ("ADB reverse active for {0}: phone http://127.0.0.1:{1} -> PC http://127.0.0.1:{1}" -f $serial, $Port) -ForegroundColor Green
& $adbPath -s $serial reverse --list | Out-Host

if ($SetPcMode) {
    & $adbPath -s $serial shell am force-stop $PackageName | Out-Null
    Set-AndroidPcMode $adbPath $serial $Port
    Write-Host ("Android app mode set to PC tools at http://127.0.0.1:{0}." -f $Port) -ForegroundColor Green
}

if ($LaunchApp) {
    & $adbPath -s $serial shell monkey -p $PackageName -c android.intent.category.LAUNCHER 1 | Out-Host
}
