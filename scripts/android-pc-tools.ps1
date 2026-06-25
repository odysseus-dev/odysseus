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
    $lines = & $AdbPath devices | Where-Object { ($_ -split "`t")[-1] -eq 'device' }
    $ids = @($lines | ForEach-Object { ($_ -split "`t")[0] })
    if ($Requested) {
        if ($ids -notcontains $Requested) {
            throw "Requested device '$Requested' is not connected. Connected devices: $($ids -join ', ')"
        }
        return $Requested
    }
    if ($ids.Count -eq 0) {
        throw "No authorized ADB device found. In Android Wireless debugging, pair/connect the phone first, then rerun this script."
    }

    # Deduplicate: a phone often appears multiple times (USB + wireless TLS variants
    # of the same physical device). Strip wireless/TLS entries that are duplicates
    # of a USB serial, then pick the USB one if available.
    $usbDevices = @($ids | Where-Object { $_ -notlike '*_adb-tls-connect._tcp' })
    $wirelessDevices = @($ids | Where-Object { $_ -like '*_adb-tls-connect._tcp' })

    # If there's exactly one USB device, use it (wireless entries are duplicates)
    if ($usbDevices.Count -eq 1) {
        if ($wirelessDevices.Count -gt 0) {
            Write-Host ("Found {0} wireless duplicate(s) of USB device '{1}', using USB connection." -f $wirelessDevices.Count, $usbDevices[0]) -ForegroundColor Yellow
        }
        return $usbDevices[0]
    }

    # Multiple distinct USB devices — genuinely can't choose
    if ($usbDevices.Count -gt 1) {
        throw "Multiple ADB devices found: $($ids -join ', '). Rerun with -Device <serial>."
    }

    # No USB devices, single wireless entry
    if ($wirelessDevices.Count -eq 1) {
        return $wirelessDevices[0]
    }

    # Multiple wireless-only entries — deduplicate by base serial
    # (e.g., "adb-XYZ" and "adb-XYZ.._adb-tls-connect._tcp" are the same device)
    $baseSerials = @{}
    foreach ($w in $wirelessDevices) {
        $base = $w -replace '( \(\d+\))?\._adb-tls-connect\._tcp$', ''
        $baseSerials[$base] = $true
    }
    if ($baseSerials.Count -eq 1) {
        $chosen = $wirelessDevices[0]
        Write-Host ("Multiple wireless entries for the same device, picking: {0}" -f $chosen) -ForegroundColor Yellow
        return $chosen
    }

    throw "Multiple ADB devices found: $($ids -join ', '). Rerun with -Device <serial>."
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
    Write-Host "Launching Android app with PC mode config via intent extras..." -ForegroundColor Cyan

    $result = & $AdbPath -s $Serial shell am start -n "$PackageName/.MainActivity" `
        -e app_mode remote `
        -e server_url $url 2>&1

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to launch app with intent extras: $result"
    }

    Write-Host "Android app launched: app_mode=remote, server_url=$url" -ForegroundColor Green
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
    & $AdbPath -s $serial shell am force-stop $PackageName | Out-Null
    Set-AndroidPcMode $adbPath $serial $Port
}
elseif ($LaunchApp) {
    & $AdbPath -s $serial shell monkey -p $PackageName -c android.intent.category.LAUNCHER 1 | Out-Host
}
