#Requires -Version 5.1
<#
.SYNOPSIS
    Windows fresh-install smoke test for Odysseus.

.DESCRIPTION
    Run before or after launch-windows.ps1 to verify prerequisites and
    installation health. Uses the same Python-detection logic as the launcher.

    Before running the launcher : checks Python 3.11+, Git, repo structure.
    After running the launcher  : also checks venv, core imports, and
                                  (if server is up) the health endpoint.

    Exit 0 = all applicable checks passed.
    Exit 1 = one or more required checks failed.

    Addresses ROADMAP.md: "Fresh install smoke tests on Windows".

.EXAMPLE
    # From repo root:
    powershell -ExecutionPolicy Bypass -File tests\smoke-windows.ps1
#>

Set-StrictMode -Version Latest

$script:Passed  = 0
$script:Failed  = 0
$script:Skipped = 0
$script:Log     = [System.Collections.Generic.List[string]]::new()

function Invoke-Check {
    param([string]$Label, [scriptblock]$Body, [switch]$Optional)
    try {
        $result = & $Body
        if ($result -eq 'SKIP') {
            $script:Skipped++
            $script:Log.Add("  SKIP  $Label")
        } else {
            $script:Passed++
            $script:Log.Add("  PASS  $Label")
        }
    } catch {
        if ($Optional) {
            $script:Skipped++
            $script:Log.Add("  SKIP  $Label -- $($_.Exception.Message)")
        } else {
            $script:Failed++
            $script:Log.Add("  FAIL  $Label -- $($_.Exception.Message)")
        }
    }
}

# Resolve repo root (this script lives in tests/)
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$VenvPy   = Join-Path $RepoRoot 'venv\Scripts\python.exe'

Write-Host "`nOdysseus -- Windows smoke test" -ForegroundColor Cyan
Write-Host "Repo : $RepoRoot"
Write-Host "================================`n"

# 1. Python 3.11+ (mirrors launcher detection logic)
Invoke-Check 'Python 3.11+ available' {
    $found = $false

    # Try py launcher first
    & py --version 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $rawVersion = [string](& py --version 2>&1)
        $m = [regex]::Match($rawVersion.Trim(), '\d+\.\d+')
        if ($m.Success) {
            $parts = $m.Value.Split('.')
            if ([int]$parts[0] -gt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11)) {
                $found = $true
            }
        }
    }

    # Fallback: try python command
    if (-not $found) {
        & python --version 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $rawVersion = [string](& python --version 2>&1)
            $m = [regex]::Match($rawVersion.Trim(), '\d+\.\d+')
            if ($m.Success) {
                $parts = $m.Value.Split('.')
                if ([int]$parts[0] -gt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11)) {
                    $found = $true
                }
            }
        }
    }

    if (-not $found) {
        throw 'Python 3.11+ not found. Install from python.org and tick "Add to PATH".'
    }
}

# 2. Git
Invoke-Check 'Git available' {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw 'Git not found. Install from https://git-scm.com/download/win'
    }
}

# 3. Repo structure
Invoke-Check 'launch-windows.ps1 present' {
    if (-not (Test-Path (Join-Path $RepoRoot 'launch-windows.ps1'))) {
        throw 'Not found -- run from inside the cloned repo.'
    }
}

Invoke-Check 'requirements.txt present' {
    if (-not (Test-Path (Join-Path $RepoRoot 'requirements.txt'))) {
        throw 'requirements.txt missing -- repo may be incomplete.'
    }
}

# 4. Venv (SKIP if not yet created -- run launch-windows.ps1 first)
Invoke-Check 'venv exists and is usable' {
    if (-not (Test-Path $VenvPy)) { return 'SKIP' }
    & $VenvPy -c 'import sys' 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'venv Python not working.' }
}

# 5. Core imports (SKIP if venv not ready)
Invoke-Check 'Core deps importable (fastapi uvicorn httpx chromadb fastembed)' {
    if (-not (Test-Path $VenvPy)) { return 'SKIP' }
    foreach ($mod in 'fastapi', 'uvicorn', 'httpx', 'chromadb', 'fastembed') {
        & $VenvPy -c "import $mod" 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "'$mod' not importable -- re-run launch-windows.ps1 to reinstall deps."
        }
    }
}

# 6. Health endpoint (optional -- only if server is running)
Invoke-Check 'API /api/version (optional)' -Optional {
    try {
        $r = Invoke-WebRequest 'http://127.0.0.1:7000/api/version' `
                               -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($r.StatusCode -ne 200) { throw "HTTP $($r.StatusCode)" }
    } catch {
        if ($_.Exception.Message -match 'refused|Unable to connect|timed? ?out') {
            return 'SKIP'
        }
        throw
    }
}

# Results
Write-Host ''
foreach ($line in $script:Log) {
    $color = if ($line -match '^  PASS') { 'Green' }
             elseif ($line -match '^  FAIL') { 'Red' }
             else { 'Yellow' }
    Write-Host $line -ForegroundColor $color
}
Write-Host ''
$msg = "Results: $($script:Passed) passed, $($script:Failed) failed, $($script:Skipped) skipped."
Write-Host $msg -ForegroundColor $(if ($script:Failed -eq 0) { 'Green' } else { 'Red' })
if ($script:Failed -eq 0) {
    Write-Host "`nAll required checks passed.`n" -ForegroundColor Green
    exit 0
} else {
    Write-Host "`nFix the FAIL items above and re-run.`n" -ForegroundColor Red
    exit 1
}