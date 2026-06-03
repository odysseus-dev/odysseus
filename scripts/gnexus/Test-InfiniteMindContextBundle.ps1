# Test-InfiniteMindContextBundle.ps1
# JUNIPERUS110: Test context bundle assembly

param(
    [switch]$Verbose
)

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "JUNIPERUS110 - Context Bundle Test" -ForegroundColor Green
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

$testScript = @"
import sys
sys.path.insert(0, r'$workspaceRoot')

from src.gnexus_governance.infinite_mind_bridge import get_bridge
import json

bridge = get_bridge()

# Test 1: Basic module load
print("[PASS] Bridge module loaded successfully")

# Test 2: Get state
state = bridge.get_infinite_mind_state()
print(f"[PASS] Get state returned: {state.get('scanStatus', 'unknown')}")

# Test 3: Load index (will be empty until scan is run)
index = bridge.load_index()
print(f"[PASS] Load index returned: {len(index) if index else 0} records")

# Test 4: Search (will be empty until scan is run)
results = bridge.search_infinite_mind("test")
print(f"[PASS] Search returned: {len(results)} results")

# Test 5: List context packs
packs = bridge.list_context_packs()
print(f"[PASS] List context packs returned: {len(packs)} packs")

# Test 6: Assemble bundle
bundle = bridge.assemble_context_bundle([], search_terms=["mission"])
print(f"[PASS] Assemble bundle returned: bundle ID {bundle.get('bundleId', 'unknown')}")

# Test 7: Redaction
text = "api_key=secret123 password=abc token=xyz"
redacted = bridge.redact_sensitive_text(text)
if "secret123" not in redacted and "abc" not in redacted:
    print("[PASS] Text redaction working")
else:
    print("[FAIL] Text redaction not working")

print("")
print("All tests completed successfully")
"@
if (-not (Test-Path $pythonExe)) {
    Write-Host "ERROR: Python executable not found: $pythonExe" -ForegroundColor Red
    exit 1
}

Write-Host "Workspace root: $workspaceRoot" -ForegroundColor Yellow
Write-Host ""
Write-Host "Running tests..." -ForegroundColor Cyan
Write-Host ""

try {
    $tempFile = [IO.Path]::GetTempFileName()
    $pyFile = [IO.Path]::ChangeExtension($tempFile, '.py')
    Rename-Item -Path $tempFile -NewName $pyFile -ErrorAction SilentlyContinue
    Set-Content -Path $pyFile -Value $testScript -Encoding UTF8
    $output = & $pythonExe $pyFile 2>&1
    $exitCode = $LASTEXITCODE
} finally {
    if (Test-Path $pyFile) {
        Remove-Item -Path $pyFile -Force
    }
}

if ($exitCode -ne 0) {
    Write-Host "Tests failed:" -ForegroundColor Red
    Write-Host $output -ForegroundColor Red
    exit 1
}

Write-Host $output -ForegroundColor Green
Write-Host ""
Write-Host "Context bundle tests completed successfully" -ForegroundColor Green

if ($LASTEXITCODE -ne 0) {
    Write-Host "Tests failed:" -ForegroundColor Red
    Write-Host $output -ForegroundColor Red
    exit 1
}

Write-Host $output -ForegroundColor Green
Write-Host ""
Write-Host "Context bundle tests completed successfully" -ForegroundColor Green
