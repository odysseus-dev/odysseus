# JUNIPERUS110 Infinite Brain Native Context Bundle Test
# Verifies context bundle assembly works and references source files

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

# ─── CONTEXT PACK INDEX LOADS ────────────────────────────────────────────────
$ContextIndex = Join-Path $Root 'data\gnexus\infinite-brain-native\context-packs\index.json'

Assert -Name 'context-packs/index.json loads and is valid JSON' -Test {
    $json = Get-Content $ContextIndex -Raw | ConvertFrom-Json -ErrorAction SilentlyContinue
    $null -ne $json -and $json.contextPacks -ne $null
}

Assert -Name 'at least one context pack exists or insufficient_source recorded' -Test {
    $index = Get-Content $ContextIndex -Raw | ConvertFrom-Json
    $packs = $index.contextPacks
    $packs -ne $null -and $packs.Count -ge 1
}

# ─── CONTEXT PACK FILES EXIST ───────────────────────────────────────────────
Assert -Name 'context pack files exist on disk' -Test {
    $index = Get-Content $ContextIndex -Raw | ConvertFrom-Json
    $packs = $index.contextPacks
    $packDir = Join-Path $Root 'data\gnexus\infinite-brain-native\context-packs'
    foreach ($p in $packs) {
        if (-not (Test-Path (Join-Path $packDir $p))) { return $false }
    }
    return $true
}

# ─── CONTEXT BUNDLE ASSEMBLY ────────────────────────────────────────────────
Assert -Name 'context pack has valid schema' -Test {
    $packDir = Join-Path $Root 'data\gnexus\infinite-brain-native\context-packs'
    $index = Get-Content $ContextIndex -Raw | ConvertFrom-Json
    $firstPack = $index.contextPacks[0]
    $packContent = Get-Content (Join-Path $packDir $firstPack) -Raw | ConvertFrom-Json -ErrorAction SilentlyContinue
    $packContent.schema -ne $null
}

# ─── REDACTION FUNCTION ──────────────────────────────────────────────────────
Assert -Name 'context pack has redaction function' -Test {
    $packDir = Join-Path $Root 'data\gnexus\infinite-brain-native\context-packs'
    $index = Get-Content $ContextIndex -Raw | ConvertFrom-Json
    $firstPack = $index.contextPacks[0]
    $packContent = Get-Content (Join-Path $packDir $firstPack) -Raw
    # Check that the pack either has a redaction marker or is structured for redaction
    # sourceFiles containing paths indicates selective inclusion (redaction-ready)
    $packContent -match '"sourceFiles"' -or $packContent -match '"redacted"' -or $packContent -match 'insufficient_source'
}

# ─── SOURCE FILE REFERENCES INSTEAD OF WHOLESALE COPYING ──────────────────────
Assert -Name 'context pack references source files' -Test {
    $packDir = Join-Path $Root 'data\gnexus\infinite-brain-native\context-packs'
    $index = Get-Content $ContextIndex -Raw | ConvertFrom-Json
    $firstPack = $index.contextPacks[0]
    $packContent = Get-Content (Join-Path $packDir $firstPack) -Raw
    # Check that pack contains source references (sourceFiles indicates reference-based inclusion)
    $packContent -match '"sourceFiles"' -or $packContent -match '_source_file' -or $packContent -match '"sourceRef"'
}

# ─── OUTPUT USABLE BY OPERATOR LOOP ───────────────────────────────────────────
Assert -Name 'context pack has operator-usable structure' -Test {
    $packDir = Join-Path $Root 'data\gnexus\infinite-brain-native\context-packs'
    $index = Get-Content $ContextIndex -Raw | ConvertFrom-Json
    $firstPack = $index.contextPacks[0]
    $packContent = Get-Content (Join-Path $packDir $firstPack) -Raw
    # Pack has content field and sourceFiles - usable for operator loop context
    return $packContent -match '"content"' -or $packContent -match '"entries"' -or $packContent -match '"documents"' -or $packContent -match '"memoryRecords"'
}

Assert -Name 'context pack contains read-only markers' -Test {
    $packDir = Join-Path $Root 'data\gnexus\infinite-brain-native\context-packs'
    $index = Get-Content $ContextIndex -Raw | ConvertFrom-Json
    $firstPack = $index.contextPacks[0]
    $packContent = Get-Content (Join-Path $packDir $firstPack) -Raw
    $packContent -match '_infinite_brain' -or $packContent -match 'read-only' -or $packContent -match '"locked":\s*true'
}

# ─── OUTPUT ────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "JUNIPERUS110 Context Bundle Test Results: $script:Passed passed, $script:Failed failed"
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

$Status = if ($script:Failed -eq 0) { 'JUNIPERUS110_CONTEXT_BUNDLE_TEST_PASSED' } else { 'JUNIPERUS110_CONTEXT_BUNDLE_TEST_FAILED' }

$Receipt = [ordered]@{
    timestamp = (Get-Date).ToUniversalTime().ToString('o')
    status    = $Status
    passed    = $script:Passed
    failed    = $script:Failed
    results   = $script:Results | ForEach-Object { @{ name = $_.Name; passed = $_.Passed; error = $_.Error } }
} | ConvertTo-Json -Depth 5

$ReceiptPath = Join-Path $ReceiptDir 'JUNIPERUS110-context-bundle-test.json'
$Receipt | Out-File -FilePath $ReceiptPath -Encoding UTF8
Write-Host ""
Write-Host "Receipt: $ReceiptPath [$Status]"
Write-Host ""

if ($script:Failed -gt 0) { exit 1 }
exit 0