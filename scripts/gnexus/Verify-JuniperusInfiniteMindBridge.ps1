# Verify-JuniperusInfiniteMindBridge.ps1
# JUNIPERUS110: Comprehensive verification that the bridge is properly implemented

param(
    [switch]$Verbose
)

$ErrorActionPreference = "Stop"
$errors = @()
$warnings = @()
$passes = 0
$fails = 0

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "JUNIPERUS110 Bridge Verification" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

function Test-Item {
    param(
        [string]$Name,
        [string]$Path,
        [string]$Type = "File"
    )
    
    Write-Host "Checking $Name..." -NoNewline
    
    if ($Type -eq "File" -and (Test-Path $Path -PathType Leaf)) {
        Write-Host " OK" -ForegroundColor Green
        $script:passes++
        return $true
    } elseif ($Type -eq "Directory" -and (Test-Path $Path -PathType Container)) {
        Write-Host " OK" -ForegroundColor Green
        $script:passes++
        return $true
    } else {
        Write-Host " FAIL" -ForegroundColor Red
        $script:errors += "$Name not found: $Path"
        $script:fails++
        return $false
    }
}

function Test-JsonValid {
    param(
        [string]$Name,
        [string]$Path
    )
    
    Write-Host "Validating $Name..." -NoNewline
    
    if (-not (Test-Path $Path)) {
        Write-Host " FAIL (not found)" -ForegroundColor Red
        $script:errors += "$Name file not found"
        $script:fails++
        return $false
    }
    
    try {
        $content = Get-Content $Path -Raw
        $json = $content | ConvertFrom-Json
        Write-Host " OK" -ForegroundColor Green
        $script:passes++
        return $true
    } catch {
        Write-Host " FAIL (invalid JSON)" -ForegroundColor Red
        $script:errors += "$Name has invalid JSON: $_"
        $script:fails++
        return $false
    }
}

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

Write-Host "Workspace root: $workspaceRoot" -ForegroundColor Yellow
Write-Host ""

# Check Python files
Write-Host "=== Python Modules ===" -ForegroundColor Cyan
Test-Item "infinite_mind_bridge.py" "$workspaceRoot\src\gnexus_governance\infinite_mind_bridge.py" "File"
Test-Item "gnexus_infinite_mind_routes.py" "$workspaceRoot\routes\gnexus_infinite_mind_routes.py" "File"

# Check UI files
Write-Host ""
Write-Host "=== UI Files ===" -ForegroundColor Cyan
Test-Item "infinite-mind.html" "$workspaceRoot\static\gnexus\infinite-mind.html" "File"
Test-Item "index.html" "$workspaceRoot\static\gnexus\index.html" "File"

# Check data structure
Write-Host ""
Write-Host "=== Data Structure ===" -ForegroundColor Cyan
Test-Item "infinite-mind directory" "$workspaceRoot\data\gnexus\infinite-mind" "Directory"
Test-Item "context-packs directory" "$workspaceRoot\data\gnexus\infinite-mind\context-packs" "Directory"

# Check data files
Write-Host ""
Write-Host "=== Data Files ===" -ForegroundColor Cyan
Test-JsonValid "source-binding.json" "$workspaceRoot\data\gnexus\infinite-mind\source-binding.json"
Test-JsonValid "writeback-policy.json" "$workspaceRoot\data\gnexus\infinite-mind\writeback-policy.json"
Test-JsonValid "scan-report.json" "$workspaceRoot\data\gnexus\infinite-mind\scan-report.json"
Test-JsonValid "file-index.json" "$workspaceRoot\data\gnexus\infinite-mind\file-index.json"
Test-JsonValid "context-packs index" "$workspaceRoot\data\gnexus\infinite-mind\context-packs\index.json"

# Check governance
Write-Host ""
Write-Host "=== Governance Checks ===" -ForegroundColor Cyan
$bindingJson = Get-Content "$workspaceRoot\data\gnexus\infinite-mind\source-binding.json" | ConvertFrom-Json
Write-Host "Checking mutationAllowed..." -NoNewline
if ($bindingJson.mutationAllowed -eq $false) {
    Write-Host " OK (locked)" -ForegroundColor Green
    $script:passes++
} else {
    Write-Host " FAIL (should be false)" -ForegroundColor Red
    $script:errors += "mutationAllowed should be false"
    $script:fails++
}

Write-Host "Checking writebackAllowed..." -NoNewline
if ($bindingJson.writebackAllowed -eq $false) {
    Write-Host " OK (locked)" -ForegroundColor Green
    $script:passes++
} else {
    Write-Host " FAIL (should be false)" -ForegroundColor Red
    $script:errors += "writebackAllowed should be false"
    $script:fails++
}

Write-Host "Checking externalCalls..." -NoNewline
if ($bindingJson.externalCalls -eq $false) {
    Write-Host " OK (disabled)" -ForegroundColor Green
    $script:passes++
} else {
    Write-Host " FAIL (should be false)" -ForegroundColor Red
    $script:errors += "externalCalls should be false"
    $script:fails++
}

Write-Host "Checking secretsStored..." -NoNewline
if ($bindingJson.secretsStored -eq $false) {
    Write-Host " OK (disabled)" -ForegroundColor Green
    $script:passes++
} else {
    Write-Host " FAIL (should be false)" -ForegroundColor Red
    $script:errors += "secretsStored should be false"
    $script:fails++
}

# Check Python syntax
Write-Host ""
Write-Host "=== Python Syntax ===" -ForegroundColor Cyan
$pythonExe = Join-Path $workspaceRoot "venv\Scripts\python.exe"
if (Test-Path $pythonExe) {
    Write-Host "Checking infinite_mind_bridge.py..." -NoNewline
    & $pythonExe -m py_compile "$workspaceRoot\src\gnexus_governance\infinite_mind_bridge.py" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host " OK" -ForegroundColor Green
        $script:passes++
    } else {
        Write-Host " FAIL" -ForegroundColor Red
        $script:errors += "infinite_mind_bridge.py has syntax errors"
        $script:fails++
    }

    Write-Host "Checking gnexus_infinite_mind_routes.py..." -NoNewline
    & $pythonExe -m py_compile "$workspaceRoot\routes\gnexus_infinite_mind_routes.py" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host " OK" -ForegroundColor Green
        $script:passes++
    } else {
        Write-Host " FAIL" -ForegroundColor Red
        $script:errors += "gnexus_infinite_mind_routes.py has syntax errors"
        $script:fails++
    }
}

# Results
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Verification Results" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Passed: $passes" -ForegroundColor Green
Write-Host "Failed: $fails" -ForegroundColor $(if ($fails -eq 0) { "Green" } else { "Red" })

if ($errors.Count -gt 0) {
    Write-Host ""
    Write-Host "Errors:" -ForegroundColor Red
    foreach ($error in $errors) {
        Write-Host "  - $error" -ForegroundColor Red
    }
}

if ($warnings.Count -gt 0) {
    Write-Host ""
    Write-Host "Warnings:" -ForegroundColor Yellow
    foreach ($warning in $warnings) {
        Write-Host "  - $warning" -ForegroundColor Yellow
    }
}

Write-Host ""
if ($fails -eq 0) {
    Write-Host "JUNIPERUS110 Bridge verification PASSED" -ForegroundColor Green
    exit 0
} else {
    Write-Host "JUNIPERUS110 Bridge verification FAILED" -ForegroundColor Red
    exit 1
}
