# Odysseus Windows Service installer (pywin32 ServiceFramework — no NSSM).
#
# Installs Odysseus as a Windows service named "Odysseus" so the FastAPI app
# and its background TaskScheduler keep running — and scheduled jobs keep
# firing — even when no terminal or desktop window is open.
#
# Usage (run as Administrator):
#   powershell -ExecutionPolicy Bypass -File install-service.ps1            # install + start
#   powershell -ExecutionPolicy Bypass -File install-service.ps1 -Action remove
#   powershell -ExecutionPolicy Bypass -File install-service.ps1 -Action status
#   powershell -ExecutionPolicy Bypass -File install-service.ps1 -Port 7000
#
# Registration is done THROUGH the runner itself
# (scripts\windows_service_runner.py install), which is a pywin32
# win32serviceutil.ServiceFramework service. That is what lets the service
# answer the SCM's start handshake (SERVICE_START_PENDING -> SERVICE_RUNNING);
# a bare sc.exe create pointed at a plain script fails with Error 1053. Logs go
# to data\service.log.

param(
    [ValidateSet("install", "remove", "status", "restart")]
    [string]$Action = "install",
    [int]$Port = 7000,
    [string]$BindHost = "0.0.0.0"
)

$ErrorActionPreference = "Stop"
$ServiceName = "Odysseus"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Host "[ERROR] This action requires Administrator. Re-run PowerShell as Administrator." -ForegroundColor Red
        exit 1
    }
}

function Get-PythonPath {
    # The service runs under console python.exe (no console is allocated for a
    # service; the runner routes stray stdout/stderr to the log). We also use it
    # here to drive the framework install/remove verbs.
    $py = Join-Path $scriptDir "venv\Scripts\python.exe"
    if (-not (Test-Path $py)) {
        Write-Host "[ERROR] venv not found at $py. Run install-windows.ps1 first." -ForegroundColor Red
        exit 1
    }
    return $py
}

$runner = Join-Path $scriptDir "scripts\windows_service_runner.py"

switch ($Action) {
    "status" {
        $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if ($svc) {
            Write-Host "Service '$ServiceName': $($svc.Status)" -ForegroundColor Cyan
            sc.exe qc $ServiceName
        } else {
            Write-Host "Service '$ServiceName' is not installed." -ForegroundColor Yellow
        }
        exit 0
    }

    "remove" {
        Assert-Admin
        $python = Get-PythonPath
        $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if ($svc) {
            if ($svc.Status -ne "Stopped") { Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue }
            # Unregister through the framework; fall back to sc.exe delete.
            & $python $runner remove 2>$null
            if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
                sc.exe delete $ServiceName | Out-Null
            }
            Write-Host "[OK] Service '$ServiceName' removed." -ForegroundColor Green
        } else {
            Write-Host "Service '$ServiceName' was not installed." -ForegroundColor Yellow
        }
        exit 0
    }

    "restart" {
        Assert-Admin
        Restart-Service -Name $ServiceName -Force
        Write-Host "[OK] Service '$ServiceName' restarted." -ForegroundColor Green
        exit 0
    }

    "install" {
        Assert-Admin
        $python = Get-PythonPath
        if (-not (Test-Path $runner)) {
            Write-Host "[ERROR] runner not found: $runner" -ForegroundColor Red
            exit 1
        }

        # Remove any existing instance first (idempotent reinstall).
        $existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if ($existing) {
            if ($existing.Status -ne "Stopped") { Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue }
            & $python $runner remove 2>$null
            if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
                sc.exe delete $ServiceName | Out-Null
            }
            Start-Sleep -Seconds 1
        }

        # The service reads HOST/PORT from machine env vars (visible to the
        # LocalSystem account the service runs under).
        [Environment]::SetEnvironmentVariable("ODYSSEUS_PORT", "$Port", "Machine")
        [Environment]::SetEnvironmentVariable("ODYSSEUS_HOST", "$BindHost", "Machine")

        Write-Host "Installing service '$ServiceName' (pywin32 framework)..." -ForegroundColor Cyan
        # HandleCommandLine registers binPath = "<venv python>" "<runner.py>"
        # and sets DisplayName/Description from the service class. --startup auto
        # makes it start at boot.
        & $python $runner --startup auto install
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[ERROR] Service registration failed (exit $LASTEXITCODE). Check pywin32 install." -ForegroundColor Red
            exit 1
        }

        # Auto-restart on unexpected failure (additive; the framework install
        # doesn't set recovery actions).
        sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/5000/restart/5000 | Out-Null

        Write-Host "Starting service..." -ForegroundColor Cyan
        Start-Service -Name $ServiceName
        Start-Sleep -Seconds 3
        $svc = Get-Service -Name $ServiceName
        if ($svc.Status -eq "Running") {
            Write-Host "[OK] Service '$ServiceName' is running on http://localhost:$Port" -ForegroundColor Green
            Write-Host "     Scheduled jobs will now fire even with no window open." -ForegroundColor Green
            Write-Host "     Logs: data\service.log" -ForegroundColor White
        } else {
            Write-Host "[WARN] Service status: $($svc.Status). Check data\service.log" -ForegroundColor Yellow
        }
        exit 0
    }
}
