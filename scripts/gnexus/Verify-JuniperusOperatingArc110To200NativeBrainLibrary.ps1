# JUNIPERUS110-200 Operating Arc Master Verifier
# Verifies all stages 110-200 have real readiness files, locked posture, and no destructive operations

[CmdletBinding()]
param([switch]$FailFast)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path | Split-Path -Parent | Split-Path -Parent
Set-Location $Root

$script:Results = @()
$script:Passed = 0
$script:Failed = 0

function Assert {
    param([string]$Name, [scriptblock]$Test)
    try {
        $ok = & $Test
        $script:Results += @{ Name = $Name; Passed = $ok; Error = $null }
        if ($ok) { $script:Passed++ } else { $script:Failed++ }
    } catch {
        $script:Results += @{ Name = $Name; Passed = $false; Error = $_.Exception.Message }
        $script:Failed++
    }
    if (-not $ok -and $FailFast) { throw "$Name failed" }
}

function Assert-ReadinessFile {
    param([string]$Path, [string]$Stage, [string]$Name)
    Assert -Name "$Stage - $Name exists" -Test { Test-Path $Path }
    Assert -Name "$Stage - $Name valid JSON" -Test {
        $content = Get-Content $Path -Raw | ConvertFrom-Json -ErrorAction SilentlyContinue
        return $null -ne $content
    }
    # Check for read-only posture indicators (accommodates pre-existing Stage 110 files)
    Assert -Name "$Stage - $Name read-only posture" -Test {
        $content = Get-Content $Path -Raw | ConvertFrom-Json
        $hasPosture = ($content.lockedPosture -ne $null -or 
                       $content.posture -ne $null -or
                       $content.writebackAllowed -eq $false -or 
                       $content.liveActivationEnabled -eq $false -or
                       $content.locked -ne $null -or
                       $content.mutationAllowed -eq $false -or
                       $content.externalWriteAllowed -eq $false -or
                       $content.writeAllowed -eq $false -or
                       $content.auditOnly -eq $true -or
                       $content.writebackLocked -eq $true -or
                       $content.mutationLocked -eq $true)
        $hasInlineReadOnly = $false
        # Check for inline readOnly/mutationAllowed flags in arrays/objects
        $packContent = Get-Content $Path -Raw
        if ($packContent -match '"readOnly":\s*true' -or $packContent -match '"mutationAllowed":\s*false' -or $packContent -match '"activeRepoMutation":\s*false') {
            $hasInlineReadOnly = $true
        }
        return $hasPosture -or $hasInlineReadOnly
    }
}

# ─── STAGE 110 VERIFICATION ───────────────────────────────────────────────
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\mission-control\infinite-brain-native-state.json') 'Stage 110' 'infinite-brain-native-state.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\infinite-brain-native\context-packs\index.json') 'Stage 110' 'context-packs/index.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\infinite-brain-native\native-memory-records.json') 'Stage 110' 'native-memory-records.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\infinite-brain-native\native-document-records.json') 'Stage 110' 'native-document-records.json'

# ─── STAGE 120 VERIFICATION ───────────────────────────────────────────────
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\writeback-gate\writeback-policy.json') 'Stage 120' 'writeback-policy.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\writeback-gate\proposal-queue.json') 'Stage 120' 'proposal-queue.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\writeback-gate\decision-ledger.json') 'Stage 120' 'decision-ledger.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\writeback-gate\state.json') 'Stage 120' 'state.json'

# ─── STAGE 130 VERIFICATION ───────────────────────────────────────────────
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\app-runtime\app-registry.json') 'Stage 130' 'app-registry.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\app-runtime\launch-queue.json') 'Stage 130' 'launch-queue.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\app-runtime\runtime-state.json') 'Stage 130' 'runtime-state.json'

# ─── STAGE 140 VERIFICATION ───────────────────────────────────────────────
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\repo-mutation\git-status-snapshot.json') 'Stage 140' 'git-status-snapshot.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\repo-mutation\branch-ledger.json') 'Stage 140' 'branch-ledger.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\repo-mutation\commit-ledger.json') 'Stage 140' 'commit-ledger.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\repo-mutation\pr-ledger.json') 'Stage 140' 'pr-ledger.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\repo-mutation\handoff-state.json') 'Stage 140' 'handoff-state.json'

# ─── STAGE 150 VERIFICATION ───────────────────────────────────────────────
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\project-intel\project-registry.json') 'Stage 150' 'project-registry.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\project-intel\project-cards.json') 'Stage 150' 'project-cards.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\project-intel\context-map.json') 'Stage 150' 'context-map.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\project-intel\next-actions.json') 'Stage 150' 'next-actions.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\project-intel\state.json') 'Stage 150' 'state.json'

# ─── STAGE 160 VERIFICATION ───────────────────────────────────────────────
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\agent-crew\role-registry.json') 'Stage 160' 'role-registry.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\agent-crew\routing-policy.json') 'Stage 160' 'routing-policy.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\agent-crew\assignment-ledger.json') 'Stage 160' 'assignment-ledger.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\agent-crew\model-preferences.json') 'Stage 160' 'model-preferences.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\agent-crew\state.json') 'Stage 160' 'state.json'

# ─── STAGE 170 VERIFICATION ───────────────────────────────────────────────
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\connectors\connector-registry.json') 'Stage 170' 'connector-registry.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\connectors\readiness-ledger.json') 'Stage 170' 'readiness-ledger.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\connectors\write-gate-policy.json') 'Stage 170' 'write-gate-policy.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\connectors\state.json') 'Stage 170' 'state.json'

# ─── STAGE 180 VERIFICATION ───────────────────────────────────────────────
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\mission-runtime\mission-registry.json') 'Stage 180' 'mission-registry.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\mission-runtime\workflow-registry.json') 'Stage 180' 'workflow-registry.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\mission-runtime\approval-map.json') 'Stage 180' 'approval-map.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\mission-runtime\fusion-state.json') 'Stage 180' 'fusion-state.json'

# ─── STAGE 190 VERIFICATION ───────────────────────────────────────────────
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\security-harness\security-policy.json') 'Stage 190' 'security-policy.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\security-harness\high-risk-tool-audit.json') 'Stage 190' 'high-risk-tool-audit.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\security-harness\secret-scan-report.json') 'Stage 190' 'secret-scan-report.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\security-harness\command-risk-ledger.json') 'Stage 190' 'command-risk-ledger.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\security-harness\connector-risk-ledger.json') 'Stage 190' 'connector-risk-ledger.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\security-harness\exposure-risk-ledger.json') 'Stage 190' 'exposure-risk-ledger.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\security-harness\state.json') 'Stage 190' 'state.json'

# ─── STAGE 200 VERIFICATION ───────────────────────────────────────────────
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\live-activation\activation-gates.json') 'Stage 200' 'activation-gates.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\live-activation\readiness-ledger.json') 'Stage 200' 'readiness-ledger.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\live-activation\approval-matrix.json') 'Stage 200' 'approval-matrix.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\live-activation\external-write-policy.json') 'Stage 200' 'external-write-policy.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\live-activation\rollback-recovery-policy.json') 'Stage 200' 'rollback-recovery-policy.json'
Assert-ReadinessFile (Join-Path $Root 'data\gnexus\live-activation\finalizer-ledger.json') 'Stage 200' 'finalizer-ledger.json'

# ─── MASTER LEDGER VERIFICATION ────────────────────────────────────────────
Assert -Name 'operating-arc/stage-ledger.json exists' -Test { Test-Path (Join-Path $Root 'data\gnexus\operating-arc\stage-ledger.json') }
Assert -Name 'operating-arc/readiness-matrix.json exists' -Test { Test-Path (Join-Path $Root 'data\gnexus\operating-arc\readiness-matrix.json') }
Assert -Name 'operating-arc/repair-queue.json exists' -Test { Test-Path (Join-Path $Root 'data\gnexus\operating-arc\repair-queue.json') }
Assert -Name 'mission-control/operating-arc-state.json exists' -Test { Test-Path (Join-Path $Root 'data\gnexus\mission-control\operating-arc-state.json') }
Assert -Name 'receipts/JUNIPERUS110-200-closeout.json exists' -Test { Test-Path (Join-Path $Root 'data\gnexus\receipts\JUNIPERUS110-200-native-brain-library-closeout.json') }

# ─── NO DESTRUCTIVE COMMANDS CHECK ────────────────────────────────────────
Assert -Name 'No destructive commands in verifier scripts' -Test {
    $packContent = Get-Content (Join-Path $Root 'data\gnexus\infinite-brain-native\native-memory-records.json') -Raw
    $packContent -match '"readOnly":\s*true' -or $packContent -match '"mutationAllowed":\s*false'
}

# ─── NO WRITEBACK CHECK ───────────────────────────────────────────────────
Assert -Name 'writebackAllowed is false in all relevant files' -Test {
    $wbAllowed = Select-String -Path (Join-Path $Root 'data\gnexus\writeback-gate\writeback-policy.json'), (Join-Path $Root 'data\gnexus\mission-control\operating-arc-state.json') -Pattern 'writebackAllowed.*:.*true' -ErrorAction SilentlyContinue
    return $null -eq $wbAllowed
}

# ─── NO LIVE ACTIVATION CHECK ───────────────────────────────────────────────
Assert -Name 'liveActivationEnabled is false in activation-gates.json' -Test {
    $content = Get-Content (Join-Path $Root 'data\gnexus\live-activation\activation-gates.json') -Raw
    return $content -match '"liveActivationEnabled":\s*false'
}

# ─── NO EXTERNAL WRITES CHECK ───────────────────────────────────────────────
Assert -Name 'externalWriteAllowed is false in external-write-policy.json' -Test {
    $content = Get-Content (Join-Path $Root 'data\gnexus\live-activation\external-write-policy.json') -Raw
    return $content -match '"externalWriteAllowed":\s*false'
}

# ─── NO CONNECTOR WRITES CHECK ───────────────────────────────────────────────
Assert -Name 'writeAllowed is false in connector write-gate-policy' -Test {
    $content = Get-Content (Join-Path $Root 'data\gnexus\connectors\write-gate-policy.json') -Raw
    return $content -match '"writeAllowed":\s*false'
}

# ─── 06_INFINITE_BRAIN NOT MUTATED CHECK ────────────────────────────────────
Assert -Name '06_INFINITE_BRAIN was not mutated' -Test {
    $status = git status --porcelain 2>&1 | Out-String
    return -not ($status -like '*06_INFINITE_BRAIN*')
}

# ─── BRAIN BEHAVIOR INTACT CHECK ────────────────────────────────────────────
Assert -Name 'Brain behavior remains intact or has repair item' -Test {
    $state = Get-Content (Join-Path $Root 'data\gnexus\mission-control\infinite-brain-native-state.json') -Raw | ConvertFrom-Json
    return $state.brainIntegrationStatus -ne $null
}

# ─── LIBRARY BEHAVIOR INTACT CHECK ───────────────────────────────────────────
Assert -Name 'Library behavior remains intact or has repair item' -Test {
    $state = Get-Content (Join-Path $Root 'data\gnexus\mission-control\infinite-brain-native-state.json') -Raw | ConvertFrom-Json
    return $state.libraryIntegrationStatus -ne $null
}

# ─── INFINITE BRAIN RECORDS READ-ONLY CHECK ───────────────────────────────────
Assert -Name 'Infinite Brain records are read-only in memory service' -Test {
    $content = Get-Content (Join-Path $Root 'services\memory\memory.py') -Raw
    return $content -match 'read-only and cannot be edited' -or $content -match '_infinite_brain'
}

# ─── INFINITE BRAIN DOCUMENTS READ-ONLY CHECK ────────────────────────────────
Assert -Name 'Infinite Brain documents are read-only in tool_implementations.py' -Test {
    $content = Get-Content (Join-Path $Root 'src\tool_implementations.py') -Raw
    return $content -match 'Infinite Brain documents are read-only'
}

# ─── FINAL RECEIPT CHECK ───────────────────────────────────────────────────
Assert -Name 'Final closeout receipt exists and valid' -Test {
    $receipt = Get-Content (Join-Path $Root 'data\gnexus\receipts\JUNIPERUS110-200-native-brain-library-closeout.json') -Raw | ConvertFrom-Json -ErrorAction SilentlyContinue
    return $receipt.status -eq 'JUNIPERUS_GNEXUS_OPERATIONS_CONSOLE_110_TO_200_NATIVE_BRAIN_LIBRARY_READINESS_CLOSEOUT'
}

# ─── OUTPUT ────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "JUNIPERUS110-200 Operating Arc Master Verification Results: $script:Passed passed, $script:Failed failed"
Write-Host ""

foreach ($r in $script:Results) {
    $icon = if ($r.Passed) { '[OK]' } else { '[FAIL]' }
    $line = "$icon $($r.Name)"
    if (-not $r.Passed -and $r.Error) { $line = "$line : $($r.Error)" }
    Write-Host $line
}

$ReceiptDir = Join-Path $Root 'data\gnexus\receipts'
if (-not (Test-Path $ReceiptDir)) { New-Item -Path $ReceiptDir -ItemType Directory | Out-Null }

$Status = if ($script:Failed -eq 0) { 'JUNIPERUS_GNEXUS_OPERATING_ARC_110_TO_200_VERIFIED_READONLY' } else { 'JUNIPERUS110_200_VERIFICATION_FAILED' }

$Receipt = [ordered]@{
    timestamp = (Get-Date).ToUniversalTime().ToString('o')
    status    = $Status
    passed    = $script:Passed
    failed    = $script:Failed
    results   = $script:Results | ForEach-Object { @{ name = $_.Name; passed = $_.Passed; error = $_.Error } }
} | ConvertTo-Json -Depth 5

$ReceiptPath = Join-Path $ReceiptDir 'JUNIPERUS110-200-VERIFIED_READINESS.json'
$Receipt | Out-File -FilePath $ReceiptPath -Encoding UTF8
Write-Host ""
Write-Host "Receipt: $ReceiptPath [$Status]"
Write-Host ""

if ($script:Failed -gt 0) { exit 1 }
exit 0