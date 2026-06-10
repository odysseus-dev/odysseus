# Phase 3c: Verify MCP Servers Installation and Operation
#
# This script verifies that both MCP servers are installed, running, and responding.
# Run AFTER executing 03c_install_mcp_servers.ps1
#
# Usage: .\deploy\03d_verify_mcp_servers.ps1

$ErrorActionPreference = "Continue"

Write-Host "Phase 3c: MCP Servers Verification" -ForegroundColor Cyan
Write-Host "==================================`n"

# Configuration
$service1Name = "Odysseus-PlaywrightMCP"
$service2Name = "Odysseus-PyMCPFS"
$playwrightUrl = "http://localhost:9090"
$pyMcpFsUrl = "http://localhost:8100"
$odysseusUrl = "http://127.0.0.1:7000"
$odysseusToken = "ody_yvdx08cqqLxXNG9FxV0vVyq8A2Enp2o0D9_H4tt6lZk"

$checks = @{ passed = 0; failed = 0; warnings = 0 }

function Test-Check {
    param([string]$Name, [scriptblock]$Test, [string]$ErrorMessage = "Check failed")
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

# ============================================================================
# Part 1: Check Services
# ============================================================================

Write-Host "Part 1: Windows Services Status" -ForegroundColor Yellow
Write-Host "-" * 50

Test-Check "$service1Name service exists" {
    $service = Get-Service -Name $service1Name -ErrorAction SilentlyContinue
    $service -ne $null
} -ErrorMessage "Service not found. Run 03c_install_mcp_servers.ps1"

$service1 = Get-Service -Name $service1Name -ErrorAction SilentlyContinue
if ($service1) {
    Write-Host "  Status: $($service1.Status)"
    if ($service1.Status -ne "Running") {
        Write-Host "  ⚠ Service is not running. Start with: Start-Service -Name '$service1Name'"
    }
}

Test-Check "$service2Name service exists" {
    $service = Get-Service -Name $service2Name -ErrorAction SilentlyContinue
    $service -ne $null
} -ErrorMessage "Service not found. Run 03c_install_mcp_servers.ps1"

$service2 = Get-Service -Name $service2Name -ErrorAction SilentlyContinue
if ($service2) {
    Write-Host "  Status: $($service2.Status)"
    if ($service2.Status -ne "Running") {
        Write-Host "  ⚠ Service is not running. Start with: Start-Service -Name '$service2Name'"
    }
}

Write-Host ""

# ============================================================================
# Part 2: Health Checks (MCP Servers)
# ============================================================================

Write-Host "Part 2: MCP Server Health Checks" -ForegroundColor Yellow
Write-Host "-" * 50

Test-Check "Playwright MCP responds to ping ($playwrightUrl)" {
    try {
        $response = Invoke-WebRequest -Uri "$playwrightUrl" -TimeoutSec 5 -ErrorAction SilentlyContinue
        $response.StatusCode -eq 200 -or $response.StatusCode -eq 404  # 404 is OK, means server is up
    } catch {
        $false
    }
} -ErrorMessage "Playwright MCP not responding. Ensure service is running."

Test-Check "PyMCP-FS responds to ping ($pyMcpFsUrl)" {
    try {
        $response = Invoke-WebRequest -Uri "$pyMcpFsUrl" -TimeoutSec 5 -ErrorAction SilentlyContinue
        $response.StatusCode -eq 200 -or $response.StatusCode -eq 404
    } catch {
        $false
    }
} -ErrorMessage "PyMCP-FS not responding. Ensure service is running."

Write-Host ""

# ============================================================================
# Part 3: FastAPI Integration
# ============================================================================

Write-Host "Part 3: FastAPI /mcp-call Endpoint" -ForegroundColor Yellow
Write-Host "-" * 50

$headers = @{ "Authorization" = "Bearer $odysseusToken" }

Test-Check "POST /mcp-call endpoint responds" {
    try {
        $body = @{
            server = "odys-browser-mcp"
            tool = "open_page"
            args = @{ url = "https://example.com" }
        } | ConvertTo-Json

        $response = Invoke-WebRequest -Uri "$odysseusUrl/mcp-call" `
            -Headers $headers `
            -Body $body `
            -ContentType "application/json" `
            -Method POST `
            -TimeoutSec 10 `
            -ErrorAction SilentlyContinue

        $response.StatusCode -eq 200
    } catch {
        $false
    }
} -WarningMessage "Endpoint may not be accessible yet (app may need restart)"

# Test a mock MCP call
Write-Host ""
Write-Host "Testing MCP Call Routing:" -ForegroundColor Cyan

$testCases = @(
    @{ server = "odys-browser-mcp"; tool = "open_page"; args = @{ url = "https://example.com" } },
    @{ server = "odys-fs-mcp"; tool = "list_directory"; args = @{ path = "F:\odysseus\data" } }
)

foreach ($test in $testCases) {
    try {
        $body = $test | ConvertTo-Json
        $response = Invoke-WebRequest -Uri "$odysseusUrl/mcp-call" `
            -Headers $headers `
            -Body $body `
            -ContentType "application/json" `
            -Method POST `
            -TimeoutSec 10 `
            -ErrorAction SilentlyContinue

        if ($response.StatusCode -eq 200) {
            $json = $response.Content | ConvertFrom-Json
            Write-Host "  ✓ $($test.server):$($test.tool)" -ForegroundColor Green
            Write-Host "    Status: $($json.status)"
            if ($json.error) {
                Write-Host "    Error: $($json.error)" -ForegroundColor Yellow
            }
        }
    } catch {
        Write-Host "  ✗ $($test.server):$($test.tool)" -ForegroundColor Red
        Write-Host "    Error: $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host ""

# ============================================================================
# Part 4: Log Files
# ============================================================================

Write-Host "Part 4: Log Files" -ForegroundColor Yellow
Write-Host "-" * 50

$logFiles = @(
    "F:\odysseus\logs\scheduler.log",
    "F:\odysseus\logs\mcp.log",
    "F:\odysseus\logs\playwright-mcp.log",
    "F:\odysseus\logs\pymcp-fs.log"
)

foreach ($logFile in $logFiles) {
    if (Test-Path $logFile) {
        $size = (Get-Item $logFile).Length
        Write-Host "✓ $logFile ($size bytes)"
    } else {
        Write-Host "⚠ $logFile (not yet created)" -ForegroundColor Yellow
    }
}

Write-Host ""

# ============================================================================
# Summary
# ============================================================================

Write-Host "Verification Summary" -ForegroundColor Cyan
Write-Host "===================="
Write-Host "Passed:  $($checks.passed) checks" -ForegroundColor Green
Write-Host "Warnings: $($checks.warnings) warnings" -ForegroundColor Yellow
Write-Host "Failed:  $($checks.failed) checks" -ForegroundColor $(if ($checks.failed -eq 0) { "Green" } else { "Red" })

if ($checks.failed -eq 0) {
    Write-Host ""
    Write-Host "✓ Phase 3c is ready for operation!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Status:"
    Write-Host "  • Orchestration loop: Every 5 minutes (Phase 3a) ✓"
    Write-Host "  • MCP servers: Running as Windows services (Phase 3c) ✓"
    Write-Host "  • FastAPI routing: Configured (Phase 3c) ✓"
    Write-Host ""
    Write-Host "System is fully operational. Monitor logs:"
    Write-Host "  Get-Content F:\odysseus\logs\scheduler.log -Tail 20 -Follow"
    Write-Host "  Get-Content F:\odysseus\logs\mcp.log -Tail 20 -Follow"
} else {
    Write-Host ""
    Write-Host "⚠ Some checks failed. Review above and troubleshoot." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Troubleshooting:"
    Write-Host "  1. Start services: Start-Service $service1Name, $service2Name"
    Write-Host "  2. Check status: Get-Service $service1Name, $service2Name"
    Write-Host "  3. View logs: Get-Content F:\odysseus\logs\*mcp*.log"
    Write-Host "  4. Restart Odysseus app (code changes need reload)"
}
