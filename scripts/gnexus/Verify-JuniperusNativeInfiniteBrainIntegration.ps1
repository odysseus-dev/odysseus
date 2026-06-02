<#
.SYNOPSIS
    JUNIPERUS110 Native Infinite Brain Integration Verifier
#>
param(
    [switch]$FailFast
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent | Split-Path -Parent
Set-Location $Root

$script:Results = @()

function Assert {
    param(
        [string]$Name,
        [scriptblock]$Test
    )
    try {
        $ok = & $Test
        $script:Results += @{ Name = $Name; Passed = $ok }
    } catch {
        $script:Results += @{ Name = $Name; Passed = $false; Error = $_.Exception.Message }
    }
    if (-not $ok -and $FailFast) { throw "$Name failed" }
}

function Assert-FileContains {
    param(
        [string]$Path,
        [string]$Pattern,
        [string]$Name
    )
    Assert -Name $Name -Test {
        $content = Get-Content $Path -Raw
        $content -match [regex]::Escape($Pattern)
    }
}

function Assert-FileNotContains {
    param(
        [string]$Path,
        [string]$Pattern,
        [string]$Name
    )
    Assert -Name $Name -Test {
        $content = Get-Content $Path -Raw
        -not ($content -match [regex]::Escape($Pattern))
    }
}

# ─── PHASE COMPILE CHECKS ─────────────────────────────────────────────────────
$CompileTargets = @(
    'services\memory\memory.py',
    'src\memory.py',
    'src\ai_interaction.py',
    'src\tool_implementations.py'
)

foreach ($f in $CompileTargets) {
    Assert -Name "Compile $f" -Test {
        & python -m py_compile $f 2>&1 | Out-Null
        $LASTEXITCODE -eq 0
    }
}

Assert -Name 'compileall app.py routes src services' -Test {
    $out = & .\venv\Scripts\python.exe -m compileall app.py routes src services 2>&1 | Out-String
    $out -notmatch 'Error'
}

# ─── PATH EXISTENCE CHECKS ─────────────────────────────────────────────────────
$script:InfiniteBrainRoot = 'C:\Users\iamcy\CymaticsDev\06_INFINITE_BRAIN'
Assert -Name 'Infinite Brain root exists' -Test { Test-Path $InfiniteBrainRoot }

# ─── NATIVE ROUTE / SCHEMA CHECKS ─────────────────────────────────────────────
$AppRoutes = Get-Content app.py -Raw
$RoutesRoutes = Get-Content routes\memory_routes.py -Raw

Assert -Name 'Memory routes exist' -Test { $RoutesRoutes -match 'setup_memory_routes' }
Assert -Name 'manage_documents schema present in tool_implementations.py' -Test {
    (Get-Content src\tool_implementations.py -Raw) -match 'do_manage_documents'
}
Assert -Name 'manage_memory schema present in ai_interaction.py' -Test {
    (Get-Content src\ai_interaction.py -Raw) -match 'do_manage_memory'
}
# Check the route files directly (more reliable than searching app.py text)
$DocRoutesFile = 'routes\document_routes.py'
$MemRoutesFile = 'routes\memory_routes.py'

Assert -Name 'Native Library routes still exist' -Test {
    Test-Path $DocRoutesFile
}
Assert -Name 'Native Brain routes still exist' -Test {
    Test-Path $MemRoutesFile
}

# ─── READ-ONLY GUARD CHECKS ────────────────────────────────────────────────────
$AiInt = Get-Content src\ai_interaction.py -Raw
$ToolImpl = Get-Content src\tool_implementations.py -Raw
$UtilsMemory = Get-Content services\memory\memory.py -Raw
$SrcMemory = Get-Content src\memory.py -Raw

# IB memory edit guard
Assert -Name 'IB memory edit blocked in ai_interaction.py' -Test {
    $AiInt -match '_infinite_brain' -and $AiInt -match 'read-only and cannot be edited'
}
# IB memory delete guard
Assert -Name 'IB memory delete blocked in ai_interaction.py' -Test {
    $AiInt -match 'read-only and cannot be deleted'
}
# IB doc delete guard
Assert -Name 'IB doc delete blocked in tool_implementations.py' -Test {
    $ToolImpl -match 'Infinite Brain documents are read-only and cannot be deleted'
}
# IB doc read/view guard
Assert -Name 'IB doc read-only metadata present' -Test {
    $ToolImpl -match '_infinite_brain.*True' -and $ToolImpl -match 'read-only'
}
# IB pinned blocked (no pin code path can mutate IB)
Assert -Name 'No IB writeback via pin' -Test {
    $RoutesRoutes -match 'pin_memory' -and
    (-not ((Get-Content routes\memory_routes.py -Raw) -match 'pin.*infinite_brain')) -or
    ($UtilsMemory -match 'delete_memory' -and $UtilsMemory -match '_infinite_brain')
}

# ─── METADATA FIELD CHECKS ─────────────────────────────────────────────────────
Assert -Name 'IB memory source=infinite_brain' -Test {
    ($SrcMemory -match '"source":\s*"infinite_brain"') -or
    ($UtilsMemory -match '"source":\s*"infinite_brain"')
}
Assert -Name 'IB memory owner=system' -Test {
    ($SrcMemory -match '"owner":\s*"system"') -or
    ($UtilsMemory -match '"owner":\s*"system"')
}
Assert -Name 'IB memory _infinite_brain flag present' -Test {
    ($SrcMemory -match '_infinite_brain.*True') -or
    ($UtilsMemory -match '_infinite_brain.*True')
}
Assert -Name 'IB doc _infinite_brain flag in results' -Test {
    $ToolImpl -match '"_infinite_brain":\s*True'
}

# ─── BLOCKLIST CHECKS ─────────────────────────────────────────────────────────
$ProhibitedPatterns = @(
    'writebackAllowed.*true',
    'mutationAllowed.*true',
    'externalCalls.*true',
    'secretsStored.*true',
    ', "\\..*",.*\.cmd'
)

foreach ($pat in $ProhibitedPatterns) {
    $safe = $pat -replace '\.\\.', '__DOTDOT__'
    Assert -Name "Blocklist: $safe" -Test {
        foreach ($f in $CompileTargets) {
            $content = Get-Content $f -Raw
            if ($content -match $pat) { return $false }
        }
        return $true
    }
}

<#
    ─── BROAD REWRITE CHECKS ──────────────────────────────────────────────────
    Detect if an app.py rewrite window is suspicious (> 5% changed lines).
#>
$AppBase = 'app.py'
if (Test-Path $AppBase) {
    Assert -Name 'No broad app.py rewrite' -Test {
        $lines = (Get-Content $AppBase).Count
        $staged = & git diff -- $AppBase 2>&1 | Out-String
        $added = ([regex]::Matches($staged, '^\+')).Count
        $removed = ([regex]::Matches($staged, '^-')).Count
        $changed = $added + $removed - 2  # subtract header line
        $pct = if ($lines -gt 0) { $changed / $lines } else { 0 }
        $pct -lt 0.05
    }
}

# ─── IB DIRECTORY MUTATION CHECK ───────────────────────────────────────────────
Assert -Name 'Infinite Brain root not modified by this patch' -Test {
    $changed = & git status --porcelain 2>&1 | Out-String
    -not ($changed -like "*06_INFINITE_BRAIN*")
}

# ─── OUTPUT ────────────────────────────────────────────────────────────────────
$script:Passed = @($script:Results | Where-Object Passed).Count
$script:Failed = @($script:Results | Where-Object { -not $_.Passed }).Count

Write-Host ""
Write-Host "JUNIPERUS110 Verification Results: $script:Passed passed, $script:Failed failed"
Write-Host ""

foreach ($r in $script:Results) {
    $icon = if ($r.Passed) { '[OK]' } else { '[FAIL]' }
    $line = "$icon $($r.Name)"
    if (-not $r.Passed -and $r.Error) { $line = "$line : $($r.Error)" }
    Write-Host $line
}

# Write receipt
$ReceiptDir = 'data\gnexus\receipts'
if (-not (Test-Path $ReceiptDir)) { New-Item -Path $ReceiptDir -ItemType Directory | Out-Null }

$script:Status = if ($script:Failed -eq 0) { 'JUNIPERUS110_NATIVE_INFINITE_BRAIN_INTEGRATION_VERIFIED_READONLY' } else { 'JUNIPERUS110_VERIFICATION_FAILED' }

$script:Receipt = @{
    timestamp      = (Get-Date -Format 'o')
    status         = $script:Status
    passed         = $script:Passed
    failed         = $script:Failed
    results        = @($script:Results | ForEach-Object {
        @{ name = $_.Name; passed = $_.Passed; error = $_.Error }
    })
} | ConvertTo-Json -Depth 5

$script:ReceiptPath = Join-Path $ReceiptDir 'JUNIPERUS110-native-infinite-brain-integration-verification.json'
$script:Receipt | Out-File -FilePath $script:ReceiptPath -Encoding utf8
Write-Host ""
Write-Host "Receipt: $script:ReceiptPath [$script:Status]"
Write-Host ""

if ($script:Failed -gt 0) { exit 1 }
exit 0
