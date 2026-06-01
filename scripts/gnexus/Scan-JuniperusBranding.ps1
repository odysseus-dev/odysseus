#Requires -Version 5.1
<#
  Repo-wide brand audit for Juniperus / Gnexus Operations Console.

  Scans for Odysseus references and classifies each occurrence:
    visible_branding | docs_history | functional_compat_key |
    internal_api_header_or_localstorage | test_fixture_or_migration

  FAILS only when VISIBLE user-facing branding ("Odysseus") is found in the
  generated Gnexus rooms (static/gnexus) or in the primary app shell title.
  Functional env keys, docs, tests, and .bak files are reported but allowed.
#>
param(
  [string]$TargetRepo = ""
)
$ErrorActionPreference = "Stop"

function Resolve-TargetRepo {
  param([string]$Given)
  if ($Given -and (Test-Path (Join-Path $Given "app.py"))) { return (Resolve-Path $Given).Path }
  $candidates = @(
    "C:\Users\iamcy\CymaticsDev\00_SYSTEMS\Juniperus",
    "C:\Users\iamcy\CymaticsDev\01_ACTIVE_REPOS\Juniperus"
  )
  foreach ($c in $candidates) {
    if (Test-Path (Join-Path $c "app.py")) { return (Resolve-Path $c).Path }
  }
  throw "Could not resolve Juniperus target repo."
}

$TargetRepo = Resolve-TargetRepo $TargetRepo
Write-Host ("Target repo: " + $TargetRepo)

$ignoreDirs = @("\.git\", "\venv\", "\node_modules\", "\__pycache__\", "\dist\", "\build\", "\.cache\", "\fastembed_cache\", "\tts_cache\", "\chroma\", "\memory_vectors\")

function Is-Ignored([string]$path) {
  foreach ($d in $ignoreDirs) { if ($path -like ("*" + $d + "*")) { return $true } }
  if ($path -like "*.bak") { return $true }
  return $false
}

function Classify([string]$path, [string]$line) {
  $p = $path.ToLower()
  if ($p -like "*\static\gnexus\*") { return "visible_branding" }
  if ($line -match "ODYSSEUS_[A-Z_]+") { return "functional_compat_key" }
  if ($line -match "X-Odysseus|odysseus-theme|odysseus-owner") { return "internal_api_header_or_localstorage" }
  if ($p -like "*\tests\*") { return "test_fixture_or_migration" }
  if ($p -like "*.md" -or $p -like "*\docs\*" -or $p -like "*license*" -or $p -like "*acknowledg*") { return "docs_history" }
  if ($p -like "*\scripts\*" -and (Split-Path $path -Leaf) -like "odysseus*") { return "functional_compat_key" }
  if ($p -like "*.service" -or $p -like "*start-macos.sh" -or $p -like "*.sh") { return "functional_compat_key" }
  return "docs_history"
}

$files = Get-ChildItem -Path $TargetRepo -Recurse -File -ErrorAction SilentlyContinue |
  Where-Object { -not (Is-Ignored $_.FullName) } |
  Where-Object { $_.Extension -in @(".py",".js",".html",".css",".json",".md",".txt",".sh",".ps1",".service",".webmanifest","") -and $_.Length -lt 2000000 }

$findings = New-Object System.Collections.ArrayList
foreach ($f in $files) {
  $matches = Select-String -Path $f.FullName -Pattern "odysseus" -SimpleMatch -CaseSensitive:$false -ErrorAction SilentlyContinue
  foreach ($m in $matches) {
    $cls = Classify $f.FullName $m.Line
    [void]$findings.Add([pscustomobject]@{
      file = $f.FullName.Substring($TargetRepo.Length).TrimStart("\")
      line = $m.LineNumber
      classification = $cls
      text = ($m.Line.Trim())
    })
  }
}

# Visible branding violations: any in static/gnexus.
$violations = @($findings | Where-Object { $_.classification -eq "visible_branding" })

# App shell title check.
$shellTitleOk = $true
$indexHtml = Join-Path $TargetRepo "static\index.html"
if (Test-Path $indexHtml) {
  $idx = Get-Content $indexHtml -Raw
  if ($idx -match "<title>([^<]*)</title>") {
    $title = $Matches[1]
    if ($title -notlike "*Juniperus*" -or $title -like "*Odysseus*") { $shellTitleOk = $false }
  }
}

$byClass = $findings | Group-Object classification | ForEach-Object { [pscustomobject]@{ classification=$_.Name; count=$_.Count } }

$report = [pscustomobject]@{
  status = if ($violations.Count -eq 0 -and $shellTitleOk) { "BRAND_SCAN_CLEAN" } else { "BRAND_SCAN_VIOLATIONS" }
  generatedAt = (Get-Date).ToUniversalTime().ToString("o")
  targetRepo = $TargetRepo
  totalFindings = $findings.Count
  byClassification = $byClass
  visibleBrandingViolations = $violations
  appShellTitleOk = $shellTitleOk
  findings = $findings
}

$outDir = Join-Path $TargetRepo "data\gnexus\completeness"
New-Item -ItemType Directory -Force $outDir | Out-Null
$outPath = Join-Path $outDir "brand-scan-report.json"
$report | ConvertTo-Json -Depth 8 | Set-Content -Path $outPath -Encoding UTF8

Write-Host ""
Write-Host ("Total Odysseus references: " + $findings.Count)
$byClass | ForEach-Object { Write-Host ("  " + $_.classification + ": " + $_.count) }
Write-Host ("Report: " + $outPath)

if ($violations.Count -gt 0) {
  Write-Host "[FAIL] Visible Odysseus branding found in Gnexus rooms:" -ForegroundColor Red
  $violations | ForEach-Object { Write-Host ("  " + $_.file + ":" + $_.line) -ForegroundColor Red }
  exit 1
}
if (-not $shellTitleOk) {
  Write-Host "[FAIL] App shell title is not Juniperus-branded." -ForegroundColor Red
  exit 1
}
Write-Host "BRAND SCAN CLEAN (functional compat keys preserved, no visible Odysseus branding)" -ForegroundColor Green
exit 0
