#Requires -Version 5.1
<#
  Versioned Windows lifecycle wrapper for a LAN-local Misumi/Odysseus host.

  Secrets are never accepted as command-line parameters. Health uses the
  process-local ODYSSEUS_API_TOKEN environment variable when readiness auth is
  required. The token is not printed or persisted by this script.
#>
param(
    [ValidateSet('Install','Uninstall','Run','Start','Stop','Restart','Status','Health','Logs')]
    [string]$Action = 'Status',
    [string]$SourceRoot = $PSScriptRoot,
    [string]$DataRoot = (Join-Path $env:LOCALAPPDATA 'Odysseus\Misumi'),
    [string]$BindHost = '0.0.0.0',
    [int]$Port = 420,
    [string]$TaskName = 'Odysseus-Misumi',
    [string]$HouseholdRoot = $env:MISUMI_HOUSEHOLD_ROOT,
    [string]$ModelHealthUrl = 'http://127.0.0.1:11434/api/tags',
    [string]$InterfaceHealthUrl = $env:MISUMI_INTERFACE_HEALTH_URL,
    [string]$ModelUrl = 'http://127.0.0.1:11434/api',
    [string]$Model = 'qwen3:8b',
    [ValidateRange(1,300)]
    [int]$RestartDelaySeconds = 10,
    [string]$LanCidr = '192.168.4.0/24',
    [switch]$InstallFirewall,
    [int]$Tail = 120
)

$ErrorActionPreference = 'Stop'

if ((Split-Path -Leaf $SourceRoot) -eq 'windows') {
    $SourceRoot = (Resolve-Path (Join-Path $SourceRoot '..\..')).Path
} else {
    $SourceRoot = (Resolve-Path $SourceRoot).Path
}
$DataRoot = [IO.Path]::GetFullPath($DataRoot)
$Python = Join-Path $SourceRoot 'venv\Scripts\python.exe'
$LogDir = Join-Path $DataRoot 'logs'
$LogPath = Join-Path $LogDir 'odysseus-host.log'
$HealthUrl = "http://127.0.0.1:$Port/api/health"
$ReadyUrl = "http://127.0.0.1:$Port/api/ready"

function Assert-Configuration {
    if ($Port -lt 1 -or $Port -gt 65535) { throw "Invalid port: $Port" }
    if ($BindHost -notin @('127.0.0.1','localhost','::1','0.0.0.0')) {
        $parsed = $null
        if (-not [Net.IPAddress]::TryParse($BindHost, [ref]$parsed)) {
            throw 'BindHost must be loopback, 0.0.0.0, or a literal host IP'
        }
    }
    if (-not (Test-Path -LiteralPath $Python)) {
        throw "Virtualenv Python not found at $Python. Run launch-windows.ps1 once in $SourceRoot."
    }
}

function Get-Listener {
    Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

function Get-HostProcess {
    $listener = Get-Listener
    if (-not $listener) { return $null }
    Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
}

function Invoke-Probe([string]$Url, [bool]$Authenticated) {
    $headers = @{}
    if ($Authenticated -and $env:ODYSSEUS_API_TOKEN) {
        $headers.Authorization = "Bearer $($env:ODYSSEUS_API_TOKEN)"
    }
    try {
        Invoke-RestMethod -Uri $Url -Headers $headers -TimeoutSec 5
    } catch {
        $status = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 'unreachable' }
        [pscustomobject]@{ ok = $false; status = $status; url = $Url; error = $_.Exception.Message }
    }
}

function Stop-Instance {
    $listener = Get-Listener
    if (-not $listener) { return }
    $process = Get-HostProcess
    $command = [string]$process.CommandLine
    if ($command -notmatch [regex]::Escape($SourceRoot) -and $command -notmatch 'uvicorn\s+app:app') {
        throw "Port $Port is owned by an unrelated process; refusing to stop PID $($listener.OwningProcess)."
    }
    Stop-Process -Id $listener.OwningProcess -Force
}

switch ($Action) {
    'Run' {
        Assert-Configuration
        New-Item -ItemType Directory -Force -Path $DataRoot,$LogDir | Out-Null
        $env:ODYSSEUS_DATA_DIR = $DataRoot
        $env:APP_BIND = $BindHost
        $env:APP_PORT = [string]$Port
        $env:AUTH_ENABLED = 'true'
        $env:LOCALHOST_BYPASS = 'false'
        $env:MISUMI_REQUIRED = 'true'
        if ($HouseholdRoot) { $env:MISUMI_HOUSEHOLD_ROOT = [IO.Path]::GetFullPath($HouseholdRoot) }
        if ($ModelHealthUrl) { $env:MISUMI_MODEL_HEALTH_URL = $ModelHealthUrl }
        if ($InterfaceHealthUrl) { $env:MISUMI_INTERFACE_HEALTH_URL = $InterfaceHealthUrl }
        if ($ModelUrl) { $env:MISUMI_MODEL_URL = $ModelUrl }
        if ($Model) { $env:MISUMI_MODEL = $Model }
        Set-Location -LiteralPath $SourceRoot
        # Windows PowerShell 5.1 turns native stderr lines into error records.
        # Uvicorn logs normally on stderr, so a global Stop preference would
        # terminate the service on its first healthy startup log line.
        $ErrorActionPreference = 'Continue'
        while ($true) {
            & $Python -m uvicorn app:app --host $BindHost --port $Port *>&1 |
                Tee-Object -FilePath $LogPath -Append
            $exitCode = $LASTEXITCODE
            "Uvicorn exited with code $exitCode; restarting in $RestartDelaySeconds seconds" |
                Tee-Object -FilePath $LogPath -Append
            Start-Sleep -Seconds $RestartDelaySeconds
        }
    }
    'Install' {
        Assert-Configuration
        New-Item -ItemType Directory -Force -Path $DataRoot,$LogDir | Out-Null
        $scriptPath = $MyInvocation.MyCommand.Path
        $argumentParts = @(
            '-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden',
            '-File',('"' + $scriptPath + '"'),'-Action','Run',
            '-SourceRoot',('"' + $SourceRoot + '"'),
            '-DataRoot',('"' + $DataRoot + '"'),
            '-BindHost',$BindHost,'-Port',[string]$Port,'-TaskName',('"' + $TaskName + '"')
        )
        if ($HouseholdRoot) { $argumentParts += @('-HouseholdRoot',('"' + $HouseholdRoot + '"')) }
        if ($ModelHealthUrl) { $argumentParts += @('-ModelHealthUrl',('"' + $ModelHealthUrl + '"')) }
        if ($InterfaceHealthUrl) { $argumentParts += @('-InterfaceHealthUrl',('"' + $InterfaceHealthUrl + '"')) }
        if ($ModelUrl) { $argumentParts += @('-ModelUrl',('"' + $ModelUrl + '"')) }
        if ($Model) { $argumentParts += @('-Model',('"' + $Model + '"')) }
        $argumentParts += @('-RestartDelaySeconds',[string]$RestartDelaySeconds)
        $arguments = $argumentParts -join ' '
        $taskAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
        $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 10 `
            -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 3650)
        Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $trigger `
            -Settings $settings -Description 'Authenticated LAN-local Misumi/Odysseus runtime' -Force | Out-Null
        if ($InstallFirewall) {
            $ruleName = "Odysseus Misumi TCP $Port LAN"
            Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
            New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
                -Protocol TCP -LocalPort $Port -RemoteAddress $LanCidr | Out-Null
        }
        Write-Output "Installed scheduled task $TaskName"
    }
    'Uninstall' {
        Stop-Instance
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Write-Output "Uninstalled scheduled task $TaskName; data preserved at $DataRoot"
    }
    'Start' {
        Assert-Configuration
        if (Get-Listener) { Write-Output "Already listening on port $Port"; break }
        Start-ScheduledTask -TaskName $TaskName
        Write-Output "Start requested for $TaskName"
    }
    'Stop' {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Stop-Instance
        Write-Output "Stopped $TaskName"
    }
    'Restart' {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Stop-Instance
        Start-ScheduledTask -TaskName $TaskName
        Write-Output "Restart requested for $TaskName"
    }
    'Status' {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        $listener = Get-Listener
        [pscustomobject]@{
            task = $TaskName
            task_state = if ($task) { [string]$task.State } else { 'not-installed' }
            listening = [bool]$listener
            port = $Port
            pid = if ($listener) { $listener.OwningProcess } else { $null }
            data_root = $DataRoot
            source_root = $SourceRoot
        }
    }
    'Health' {
        [pscustomobject]@{
            liveness = Invoke-Probe $HealthUrl $false
            readiness = Invoke-Probe $ReadyUrl $true
        }
    }
    'Logs' {
        if (Test-Path -LiteralPath $LogPath) { Get-Content -LiteralPath $LogPath -Tail $Tail }
        else { Write-Output "No log at $LogPath" }
    }
}
