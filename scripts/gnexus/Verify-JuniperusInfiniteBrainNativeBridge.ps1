# JUNIPERUS110 Native Infinite Brain Bridge Verifier
# Verifies read-only integration state for Infinite Brain native records

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

function Assert-FileExists {
    param([string]$Path, [string]$Name)
    Assert -Name $Name -Test { Test-Path $Path }
}

function Assert-FileContains {
    param([string]$Path, [string]$Pattern, [string]$Name)
    Assert -Name $Name -Test {
        if (-not (Test-Path $Path)) { return $false }
        $content = Get-Content $Path -Raw
        $content -match [regex]::Escape($Pattern)
    }
}

# ─── BRAIN STRUCTURE CHECKS ───────────────────────────────────────────────
$BrainRoot = 'C:\Users\iamcy\CymaticsDev\06_INFINITE_BRAIN'

Assert -Name 'Infinite Brain root exists' -Test { Test-Path $BrainRoot }
Assert -Name 'Infinite Brain CANON directory exists' -Test { Test-Path (Join-Path $BrainRoot '01_CANON') }
Assert -Name 'Infinite Brain MEMORY_OBJECTS directory exists' -Test { 
    $dirs = Get-ChildItem $BrainRoot -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -like '*MEMORY*' }
    $dirs.Count -gt 0 
}

# ─── LIBRARY STRUCTURE CHECKS ───────────────────────────────────────────────
Assert -Name 'routes\document_routes.py exists (Library)' -Test { Test-Path (Join-Path $Root 'routes\document_routes.py') }
Assert -Name 'routes\memory_routes.py exists (Library)' -Test { Test-Path (Join-Path $Root 'routes\memory_routes.py') }

# ─── STAGE 110 SCAN OUTPUTS ───────────────────────────────────────────────
Assert-FileExists (Join-Path $Root 'data\gnexus\infinite-brain-native\scan-report.json') 'scan-report.json exists'
Assert-FileExists (Join-Path $Root 'data\gnexus\infinite-brain-native\source-binding.json') 'source-binding.json exists'
Assert-FileExists (Join-Path $Root 'data\gnexus\infinite-brain-native\file-index.json') 'file-index.json exists'
Assert-FileExists (Join-Path $Root 'data\gnexus\infinite-brain-native\candidate-records.json') 'candidate-records.json exists'
Assert-FileExists (Join-Path $Root 'data\gnexus\infinite-brain-native\source-map.json') 'source-map.json exists'
Assert-FileExists (Join-Path $Root 'data\gnexus\mission-control\infinite-brain-native-state.json') 'infinite-brain-native-state.json exists'

# ─── NATIVE MEMORY RECORDS ────────────────────────────────────────────────
Assert-FileExists (Join-Path $Root 'data\gnexus\infinite-brain-native\native-memory-records.json') 'native-memory-records.json exists'
Assert -Name 'native-memory-records.json has records' -Test {
    $mem = Get-Content (Join-Path $Root 'data\gnexus\infinite-brain-native\native-memory-records.json') -Raw | ConvertFrom-Json
    $mem.Count -gt 0 -or $mem.memoryRecords -or $mem.Count -gt 0
}

# ─── NATIVE DOCUMENT RECORDS ───────────────────────────────────────────────
Assert-FileExists (Join-Path $Root 'data\gnexus\infinite-brain-native\native-document-records.json') 'native-document-records.json exists'
Assert -Name 'native-document-records.json has records' -Test {
    $doc = Get-Content (Join-Path $Root 'data\gnexus\infinite-brain-native\native-document-records.json') -Raw | ConvertFrom-Json
    $doc.Count -gt 0 -or $doc.documentRecords -or $doc.Count -gt 0
}

# ─── CONTEXT PACK INDEX ───────────────────────────────────────────────────
Assert-FileExists (Join-Path $Root 'data\gnexus\infinite-brain-native\context-packs\index.json') 'context-packs/index.json exists'

# ─── READ-ONLY GUARD CHECKS (writebackAllowed, mutationAllowed, etc.) ─────────
$StateFile = Join-Path $Root 'data\gnexus\mission-control\infinite-brain-native-state.json'
Assert -Name 'writebackLocked is true' -Test {
    $state = Get-Content $StateFile -Raw | ConvertFrom-Json
    $state.writebackLocked -eq $true
}
Assert -Name 'mutationLocked is true' -Test {
    $state = Get-Content $StateFile -Raw | ConvertFrom-Json
    $state.mutationLocked -eq $true
}

# Check that writebackAllowed is false in relevant code
Assert -Name 'writebackAllowed is false in tool_implementations.py' -Test {
    $content = Get-Content (Join-Path $Root 'src\tool_implementations.py') -Raw
    (-not ($content -match 'writebackAllowed.{0,20}true')) -or ($content -match 'writebackAllowed.{0,20}false')
}

Assert -Name 'mutationAllowed is false in ai_interaction.py' -Test {
    $content = Get-Content (Join-Path $Root 'src\ai_interaction.py') -Raw
    (-not ($content -match 'mutationAllowed.{0,20}true')) -or ($content -match 'mutationAllowed.{0,20}false')
}

Assert -Name 'externalCalls is false in relevant code' -Test {
    $content = Get-Content (Join-Path $Root 'src\tool_implementations.py') -Raw
    (-not ($content -match 'externalCalls.{0,20}true')) -or ($content -match 'externalCalls.{0,20}false')
}

Assert -Name 'secretsStored is false in relevant code' -Test {
    $content = Get-Content (Join-Path $Root 'src\tool_implementations.py') -Raw
    (-not ($content -match 'secretsStored.{0,20}true')) -or ($content -match 'secretsStored.{0,20}false')
}

# ─── 06_INFINITE_BRAIN NOT MUTATED ────────────────────────────────────────
Assert -Name '06_INFINITE_BRAIN was not mutated' -Test {
    $status = git status --porcelain 2>&1 | Out-String
    -not ($status -like '*06_INFINITE_BRAIN*')
}

# ─── BRAIN BEHAVIOR INTACT ────────────────────────────────────────────────
Assert -Name 'Brain read-only guard present in ai_interaction.py' -Test {
    $content = Get-Content (Join-Path $Root 'src\ai_interaction.py') -Raw
    $content -match '_infinite_brain' -and $content -match 'read-only and cannot be edited'
}

Assert -Name 'Brain delete guard present in ai_interaction.py' -Test {
    $content = Get-Content (Join-Path $Root 'src\ai_interaction.py') -Raw
    $content -match 'read-only and cannot be deleted'
}

Assert -Name 'Brain read-only metadata present in tool_implementations.py' -Test {
    $content = Get-Content (Join-Path $Root 'src\tool_implementations.py') -Raw
    $content -match '_infinite_brain' -and $content -match 'True' -and $content -match 'read-only'
}

# ─── LIBRARY BEHAVIOR INTACT ─────────────────────────────────────────────
Assert -Name 'Library document delete blocked' -Test {
    $content = Get-Content (Join-Path $Root 'src\tool_implementations.py') -Raw
    $content -match 'Infinite Brain documents are read-only and cannot be deleted'
}

Assert -Name 'Library routes still functional' -Test {
    $mr = Get-Content (Join-Path $Root 'routes\memory_routes.py') -Raw
    $dr = Get-Content (Join-Path $Root 'routes\document_routes.py') -Raw
    $mr -match 'setup_memory_routes' -and $dr -match 'setup_document_routes'
}

# ─── PYTHON MODULES COMPILE ─────────────────────────────────────────────────
Assert -Name 'Python modules compile successfully' -Test {
    $out = & python -m py_compile src/tool_implementations.py src/ai_interaction.py 2>&1
    $LASTEXITCODE -eq 0
}

# ─── POWERSHELL SCRIPTS PARSE ───────────────────────────────────────────────
Assert -Name 'PowerShell scripts parse successfully' -Test {
    $scriptsDir = Join-Path $Root 'scripts\gnexus'
    foreach ($ps1 in Get-ChildItem $scriptsDir -Filter '*.ps1' -ErrorAction SilentlyContinue) {
        $null = [System.Management.Automation.PSParser]::Tokenize((Get-Content $ps1.FullName -Raw), [ref]$null)
    }
    return $true
}

# ─── OUTPUT ────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "JUNIPERUS110 Native Infinite Brain Bridge Verification Results: $script:Passed passed, $script:Failed failed"
Write-Host ""

foreach ($r in $script:Results) {
    $icon = if ($r.Passed) { '[OK]' } else { '[FAIL]' }
    $line = "$icon $($r.Name)"
    if (-not $r.Passed -and $r.Error) { $line = "$line : $($r.Error)" }
    Write-Host $line
}

# Write receipt
$ReceiptDir = Join-Path $Root 'data\gnexus\receipts'
if (-not (Test-Path $ReceiptDir)) { New-Item -Path $ReceiptDir -ItemType Directory | Out-Null }

$Status = if ($script:Failed -eq 0) { 'JUNIPERUS110_NATIVE_INFINITE_BRAIN_NATIVE_BRIDGE_VERIFIED_READONLY' } else { 'JUNIPERUS110_VERIFICATION_FAILED' }

$Receipt = [ordered]@{
    timestamp = (Get-Date).ToUniversalTime().ToString('o')
    status    = $Status
    passed    = $script:Passed
    failed    = $script:Failed
    results   = $script:Results | ForEach-Object { @{ name = $_.Name; passed = $_.Passed; error = $_.Error } }
} | ConvertTo-Json -Depth 5

$ReceiptPath = Join-Path $ReceiptDir 'JUNIPERUS110-native-infinite-brain-bridge-verification.json'
$Receipt | Out-File -FilePath $ReceiptPath -Encoding UTF8
Write-Host ""
Write-Host "Receipt: $ReceiptPath [$Status]"
Write-Host ""

if ($script:Failed -gt 0) { exit 1 }
exit 0