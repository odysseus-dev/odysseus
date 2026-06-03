# Scan-InfiniteMind.ps1
# JUNIPERUS110: Scan 06_INFINITE_BRAIN and index safe files
# This is a read-only operation that does not mutate the source

param(
    [string]$MaxSizeMB = 10,
    [switch]$Verbose
)

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "JUNIPERUS110 - Infinite Mind Scanner" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Detect workspace root - more robust detection
$workspaceRoot = $null
if ($PSScriptRoot -and (Test-Path $PSScriptRoot)) {
    $workspaceRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
} elseif ((Get-Location).Provider.Name -eq "FileSystem") {
    # Fallback: look for common markers
    $current = (Get-Location).Path
    for ($i = 0; $i -lt 10; $i++) {
        if (Test-Path (Join-Path $current "venv") -PathType Container) {
            $workspaceRoot = $current
            break
        }
        $current = Split-Path -Parent $current
    }
}

# Final fallback
if (-not $workspaceRoot) {
    $workspaceRoot = "C:\Users\iamcy\CymaticsDev\00_SYSTEMS\Juniperus"
}

$pythonExe = Join-Path $workspaceRoot "venv\Scripts\python.exe"
$infiniteMindScript = @"
import sys
sys.path.insert(0, r'$workspaceRoot')

from src.gnexus_governance.infinite_mind_bridge import get_bridge

bridge = get_bridge()
report = bridge.scan_infinite_mind(max_size_mb=$MaxSizeMB)

import json
print(json.dumps(report, indent=2, default=str))
"@

if (-not (Test-Path $pythonExe)) {
    Write-Host "ERROR: Python executable not found: $pythonExe" -ForegroundColor Red
    exit 1
}

Write-Host "Workspace root: $workspaceRoot" -ForegroundColor Yellow
Write-Host "Max file size: ${MaxSizeMB}MB" -ForegroundColor Yellow
Write-Host ""

Write-Host "Running scan..." -ForegroundColor Cyan
$output = & $pythonExe -c $infiniteMindScript 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Host "Scan failed:" -ForegroundColor Red
    Write-Host $output -ForegroundColor Red
    exit 1
}

Write-Host $output -ForegroundColor Green
Write-Host ""
Write-Host "Scan completed successfully" -ForegroundColor Green
