#Requires -Version 5.1
<# Disabled-by-default Windows task definitions for Misumi Phase A pilots. #>
param(
    [ValidateSet('Install','Uninstall','Status','Run')]
    [string]$Action = 'Status',
    [ValidateSet('morning-status','skill-audit','task-triage')]
    [string]$Pilot = 'morning-status',
    [string]$SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$DataRoot = (Join-Path $env:LOCALAPPDATA 'Odysseus\Misumi'),
    [string]$HouseholdRoot = $env:MISUMI_HOUSEHOLD_ROOT,
    [string]$TaskPrefix = 'Misumi-Pilot'
)

$ErrorActionPreference = 'Stop'
$SourceRoot = (Resolve-Path $SourceRoot).Path
$DataRoot = [IO.Path]::GetFullPath($DataRoot)
$Python = Join-Path $SourceRoot 'venv\Scripts\python.exe'
$Runner = Join-Path $SourceRoot 'scripts\run_misumi_pilot.py'
$ConfigDir = Join-Path $DataRoot 'misumi'
$ConfigPath = Join-Path $ConfigDir 'autonomy.json'
$VersionedConfig = Join-Path $SourceRoot 'config\misumi_autonomy.json'
$Definitions = @(
    [pscustomobject]@{ pilot = 'morning-status'; time = '08:00'; weekly = $false },
    [pscustomobject]@{ pilot = 'task-triage'; time = '08:05'; weekly = $false },
    [pscustomobject]@{ pilot = 'skill-audit'; time = '02:00'; weekly = $true }
)

function Assert-Configuration {
    if (-not (Test-Path -LiteralPath $Python)) { throw "Python not found: $Python" }
    if (-not (Test-Path -LiteralPath $Runner)) { throw "Pilot runner not found: $Runner" }
    if (-not (Test-Path -LiteralPath $VersionedConfig)) { throw "Pilot config not found: $VersionedConfig" }
}

function Task-Name([string]$Name) { "$TaskPrefix-$Name" }

switch ($Action) {
    'Install' {
        Assert-Configuration
        New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
        if (-not (Test-Path -LiteralPath $ConfigPath)) {
            Copy-Item -LiteralPath $VersionedConfig -Destination $ConfigPath
        }
        foreach ($definition in $Definitions) {
            $taskName = Task-Name $definition.pilot
            $parts = @(
                '-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden',
                '-File',('"' + $MyInvocation.MyCommand.Path + '"'),'-Action','Run',
                '-Pilot',$definition.pilot,'-SourceRoot',('"' + $SourceRoot + '"'),
                '-DataRoot',('"' + $DataRoot + '"'),'-TaskPrefix',('"' + $TaskPrefix + '"')
            )
            if ($HouseholdRoot) { $parts += @('-HouseholdRoot',('"' + $HouseholdRoot + '"')) }
            $taskAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ($parts -join ' ')
            $at = [datetime]::Today.Add([timespan]::Parse($definition.time))
            if ($definition.weekly) {
                $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At $at
            } else {
                $trigger = New-ScheduledTaskTrigger -Daily -At $at
            }
            $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable
            Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $trigger `
                -Settings $settings -Description "Disabled-by-default read-only Misumi pilot: $($definition.pilot)" -Force | Out-Null
            Disable-ScheduledTask -TaskName $taskName | Out-Null
        }
        Write-Output "Installed disabled pilot definitions; host-local config: $ConfigPath"
    }
    'Uninstall' {
        foreach ($definition in $Definitions) {
            Unregister-ScheduledTask -TaskName (Task-Name $definition.pilot) -Confirm:$false -ErrorAction SilentlyContinue
        }
        Write-Output "Removed pilot task definitions; preserved output and config under $ConfigDir"
    }
    'Status' {
        $config = if (Test-Path -LiteralPath $ConfigPath) {
            Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
        } else { $null }
        foreach ($definition in $Definitions) {
            $task = Get-ScheduledTask -TaskName (Task-Name $definition.pilot) -ErrorAction SilentlyContinue
            $pilotConfig = if ($config) { $config.pilots.PSObject.Properties[$definition.pilot].Value } else { $null }
            [pscustomobject]@{
                pilot = $definition.pilot
                task_state = if ($task) { [string]$task.State } else { 'not-installed' }
                global_enabled = if ($config) { [bool]$config.enabled } else { $false }
                pilot_enabled = if ($pilotConfig) { [bool]$pilotConfig.enabled } else { $false }
                writes_allowed = $false
            }
        }
    }
    'Run' {
        Assert-Configuration
        if (-not (Test-Path -LiteralPath $ConfigPath)) { throw "Host-local pilot config not found: $ConfigPath" }
        $env:ODYSSEUS_DATA_DIR = $DataRoot
        $env:MISUMI_AUTONOMY_CONFIG = $ConfigPath
        if ($HouseholdRoot) { $env:MISUMI_HOUSEHOLD_ROOT = [IO.Path]::GetFullPath($HouseholdRoot) }
        Set-Location -LiteralPath $SourceRoot
        & $Python $Runner $Pilot
        exit $LASTEXITCODE
    }
}
