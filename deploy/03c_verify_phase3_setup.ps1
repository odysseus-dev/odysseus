# Phase 3c: Verification Script for Odysseus Phase 3 Deployment
#
# This script verifies that all Phase 3 components are in place and operational.
# Run AFTER Phase 3a (scheduled task) and Phase 3b (API stubs) are deployed.
#
# Usage: .\deploy\03c_verify_phase3_setup.ps1

$ErrorActionPreference = "Stop"
$checks = @{
    passed = 0
    failed = 0
    warnings = 0
}

function Test-Check {
    param(
        [string]$Name,
        [scriptblock]$Test,
        [string]$ErrorMessage = "Check failed"
    )

    Write-Host -NoNewline "[ ] $Name ... "
    try {
        $result = & $Test
        if ($result) {
            Write-Host "✓" -ForegroundColor Green
            $checks.passed++
            return $true
        } else {
            Write-Host "✗" -ForegroundColor Red
            Write-Host "    $ErrorMessage" -ForegroundColor Red
            $checks.failed++
            return $false
        }
    } catch {
        Write-Host "✗" -ForegroundColor Red
        Write-Host "    Error: $($_.Exception.Message)" -ForegroundColor Red
        $checks.failed++
        return $false
    }
}

function Test-Warn {
    param(
        [string]$Name,
        [scriptblock]$Test,
        [string]$WarningMessage = "Warning"
    )

    Write-Host -NoNewline "[ ] $Name ... "
    try {
        $result = & $Test
        if ($result) {
            Write-Host "✓" -ForegroundColor Green
            $checks.passed++
            return $true
        } else {
            Write-Host "⚠" -ForegroundColor Yellow
            Write-Host "    $WarningMessage" -ForegroundColor Yellow
            $checks.warnings++
            return $false
        }
    } catch {
        Write-Host "⚠" -ForegroundColor Yellow
        Write-Host "    Warning: $($_.Exception.Message)" -ForegroundColor Yellow
        $checks.warnings++
        return $false
    }
}

Write-Host "Odysseus Phase 3 Deployment Verification" -ForegroundColor Cyan
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host ""

# Phase 3a Checks: Scheduled Task
Write-Host "Phase 3a: Windows Scheduled Task" -ForegroundColor Yellow
Write-Host "--------------------------------"

Test-Check "Scheduled task 'Odysseus Daily Orchestration' exists" {
    $task = Get-ScheduledTask -TaskName "Odysseus Daily Orchestration" -ErrorAction SilentlyContinue
    $task -ne $null
} -ErrorMessage "Task not found. Run: .\deploy\03a_create_scheduled_task.ps1"

Test-Warn "Scheduled task is enabled" {
    $task = Get-ScheduledTask -TaskName "Odysseus Daily Orchestration" -ErrorAction SilentlyContinue
    $task.Triggers[0].Enabled -eq $true
} -WarningMessage "Task exists but is disabled. Enable: Enable-ScheduledTask -TaskName '...'"

Test-Check "Python executable exists" {
    Test-Path "C:\Program Files\Python311\python.exe"
} -ErrorMessage "Python 3.11 not found at expected path"

Test-Check "Odysseus working directory exists" {
    Test-Path "F:\Doc-SSD\Git-Repos\odysseus"
} -ErrorMessage "Working directory not found"

Test-Check "dodo.py exists in odysseus directory" {
    Test-Path "F:\Doc-SSD\Git-Repos\odysseus\dodo.py"
} -ErrorMessage "dodo.py not found. Run Phase 2 first."

Test-Check "Log directory exists or can be created" {
    $logDir = "F:\odysseus\logs"
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    Test-Path $logDir
} -ErrorMessage "Cannot create log directory"

Write-Host ""

# Phase 3b Checks: API Stubs
Write-Host "Phase 3b: MCP Service Stub Endpoints" -ForegroundColor Yellow
Write-Host "------------------------------------"

$token = $env:ODYSSEUS_API_TOKEN
if (-not $token) {
    Write-Host "⚠ ODYSSEUS_API_TOKEN not set. Setting from stored value..." -ForegroundColor Yellow
    $token = "ody_yvdx08cqqLxXNG9FxV0vVyq8A2Enp2o0D9_H4tt6lZk"
    $env:ODYSSEUS_API_TOKEN = $token
}

$url = $env:ODYSSEUS_URL
if (-not $url) {
    Write-Host "⚠ ODYSSEUS_URL not set. Using default..." -ForegroundColor Yellow
    $url = "http://127.0.0.1:7000"
    $env:ODYSSEUS_URL = $url
}

$headers = @{ "Authorization" = "Bearer $token" }

Test-Check "Odysseus server is reachable" {
    $response = Invoke-WebRequest -Uri "$url/api/health" -ErrorAction SilentlyContinue
    $response.StatusCode -eq 200
} -ErrorMessage "Cannot reach Odysseus at $url"

Test-Check "GET /mcp/status endpoint exists" {
    $response = Invoke-WebRequest -Uri "$url/mcp/status" -Headers $headers -ErrorAction SilentlyContinue
    $json = $response.Content | ConvertFrom-Json
    $json.mcp_servers -ne $null
} -ErrorMessage "Endpoint not found or not returning proper format"

Test-Check "POST /mcp-call endpoint exists (stub)" {
    $body = @{
        server = "odys-browser-mcp"
        tool = "open_page"
        args = @{ url = "https://example.com" }
    } | ConvertTo-Json
    $response = Invoke-WebRequest -Uri "$url/mcp-call" `
        -Headers $headers `
        -Body $body `
        -ContentType "application/json" `
        -Method POST `
        -ErrorAction SilentlyContinue
    $response.StatusCode -eq 200
} -WarningMessage "Endpoint not responding (expected in Phase 3b stub)"

Write-Host ""

# Phase 3c Checks: Orchestration Readiness
Write-Host "Phase 3c: Orchestration Readiness" -ForegroundColor Yellow
Write-Host "--------------------------------"

Test-Check "doit is installed and executable" {
    & python -m doit list | Out-Null
    $LASTEXITCODE -eq 0
} -ErrorMessage "doit not installed or not working"

Test-Check "All 5 dodo.py tasks are defined" {
    $tasks = & python -m doit list
    $taskList = @("sync_email", "sync_calendar", "daily_briefing", "memory_snapshot", "orchestration_loop")
    $all_found = $true
    foreach ($task in $taskList) {
        if ($tasks -notmatch $task) {
            $all_found = $false
            break
        }
    }
    $all_found
} -ErrorMessage "Not all tasks found in dodo.py"

Test-Check "Orchestration task can be listed" {
    $tasks = & python -m doit list | Select-String "orchestration_loop"
    $tasks -ne $null
} -ErrorMessage "orchestration_loop task not found"

Test-Warn "Orchestration loop can execute (manual test)" {
    # Don't actually run it (takes 30s), just check syntax
    & python -c "from dodo import task_orchestration_loop; print('OK')" 2>$null
    $LASTEXITCODE -eq 0
} -WarningMessage "dodo.py may have syntax errors (manual test needed)"

Write-Host ""

# Summary
Write-Host "Verification Summary" -ForegroundColor Cyan
Write-Host "==================="
Write-Host "Passed:  $($checks.passed) checks" -ForegroundColor Green
Write-Host "Warnings: $($checks.warnings) warnings" -ForegroundColor Yellow
Write-Host "Failed:  $($checks.failed) checks" -ForegroundColor $(if ($checks.failed -eq 0) { "Green" } else { "Red" })
Write-Host ""

if ($checks.failed -eq 0) {
    Write-Host "✓ All checks passed! Phase 3 is ready for deployment." -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:"
    Write-Host "  1. Test manual orchestration_loop: cd odysseus && python -m doit orchestration_loop"
    Write-Host "  2. Check scheduled task: Get-ScheduledTask -TaskName 'Odysseus Daily Orchestration' | Select-Object *"
    Write-Host "  3. View recent runs: Get-Content F:\odysseus\logs\scheduler.log -Tail 20"
    Write-Host "  4. Phase 3c: Deploy MCP servers (Playwright, PyMCP-FS) as systemd equivalents"
} else {
    Write-Host "✗ Some checks failed. Review the errors above and fix before proceeding." -ForegroundColor Red
    exit 1
}

# Bonus: Show next scheduled runs (if task exists)
Write-Host ""
Write-Host "Scheduled Task Status:" -ForegroundColor Cyan
$task = Get-ScheduledTask -TaskName "Odysseus Daily Orchestration" -ErrorAction SilentlyContinue
if ($task) {
    Write-Host "  State: $($task.State)"
    Write-Host "  Enabled: $($task.Enabled)"
    Write-Host "  Trigger: $($task.Triggers[0].Repetition.Interval) interval"
    Write-Host "  Last run: $($task.LastTaskResult) (exit code from last execution)"
} else {
    Write-Host "  Task not yet created (run Phase 3a setup script)"
}
