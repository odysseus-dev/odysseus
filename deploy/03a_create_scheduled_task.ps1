# Phase 3a: Create Windows Scheduled Task for Odysseus Orchestration Loop
#
# This script creates a scheduled task that runs `doit orchestration_loop` every 5 minutes.
# RUN AS ADMINISTRATOR
#
# Usage: .\deploy\03a_create_scheduled_task.ps1
#
# Do NOT run until reviewed and approved.

$ErrorActionPreference = "Stop"

# Configuration
$taskName = "Odysseus Daily Orchestration"
$taskDescription = "Runs doit orchestration_loop every 5 minutes (sync email, calendar, briefing, memory snapshot)"
$pythonExe = "C:\Program Files\Python311\python.exe"
$workingDir = "F:\Doc-SSD\Git-Repos\odysseus"
$logFile = "F:\odysseus\logs\scheduler.log"
$runUser = "$env:USERDOMAIN\$env:USERNAME"

# Verify prerequisites
Write-Host "Checking prerequisites..."
if (-not (Test-Path $pythonExe)) {
    Write-Host "✗ ERROR: Python not found at $pythonExe" -ForegroundColor Red
    exit 1
}
Write-Host "✓ Python found: $pythonExe"

if (-not (Test-Path $workingDir)) {
    Write-Host "✗ ERROR: Working directory not found: $workingDir" -ForegroundColor Red
    exit 1
}
Write-Host "✓ Working directory exists: $workingDir"

# Create log directory if it doesn't exist
$logDir = Split-Path $logFile -Parent
if (-not (Test-Path $logDir)) {
    Write-Host "Creating log directory: $logDir"
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}
Write-Host "✓ Log directory ready: $logDir"

# Check if task already exists
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Host "⚠ Task '$taskName' already exists. Removing old task..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
}

# Create trigger (every 5 minutes)
$trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 5) -Once -At (Get-Date)

# Create action
# Note: We wrap the Python call in cmd /c to ensure proper working directory and logging
$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"cd /d `"$workingDir`" && `"$pythonExe`" -m doit orchestration_loop >> `"$logFile`" 2>&1`"" `
    -WorkingDirectory $workingDir

# Create task settings
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 5)

# Create principal (run as current user, with highest privileges)
$principal = New-ScheduledTaskPrincipal -UserId $runUser -RunLevel Highest

# Register the scheduled task
Write-Host "`nCreating scheduled task..."
Write-Host "  Name: $taskName"
Write-Host "  Description: $taskDescription"
Write-Host "  Trigger: Every 5 minutes"
Write-Host "  Run as: $runUser (elevated)"
Write-Host "  Working directory: $workingDir"
Write-Host "  Logs to: $logFile"
Write-Host ""

try {
    $task = Register-ScheduledTask `
        -TaskName $taskName `
        -Trigger $trigger `
        -Action $action `
        -Settings $settings `
        -Principal $principal `
        -Description $taskDescription `
        -Force `
        -ErrorAction Stop

    Write-Host "✓ Scheduled task created successfully!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Task Details:"
    Write-Host "  Path: $($task.TaskPath)"
    Write-Host "  Enabled: $($task.Triggers[0].Enabled)"
    Write-Host "  Next run: (will be scheduled shortly)"

    # Show next scheduled runs
    Write-Host ""
    Write-Host "Next 3 scheduled runs:"
    $taskInfo = Get-ScheduledTask -TaskName $taskName
    if ($taskInfo.State -eq "Ready") {
        Write-Host "  Task is ready to run"
    }

} catch {
    Write-Host "✗ Failed to create scheduled task:" -ForegroundColor Red
    Write-Host "  $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Check logs: Get-Content -Path '$logFile' -Tail 20"
Write-Host "  2. View task: Get-ScheduledTask -TaskName '$taskName'"
Write-Host "  3. Manual test: & '$pythonExe' -m doit orchestration_loop"
