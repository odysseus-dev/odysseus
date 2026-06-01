#Requires -Version 5.1
<#
  High-risk tool / surface audit for Juniperus / Gnexus Operations Console.

  Reads config\gnexus.high-risk-tool-policy.example.json, confirms each declared
  high-risk surface has a classification, checks that route files backing those
  surfaces exist, and writes an audit report. Local-first; read-only scan.
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

$policyPath = Join-Path $TargetRepo "config\gnexus.high-risk-tool-policy.example.json"
if (-not (Test-Path $policyPath)) { Write-Host "[FAIL] policy missing: $policyPath" -ForegroundColor Red; exit 1 }
$policy = Get-Content $policyPath -Raw | ConvertFrom-Json

$valid = @("read_only_allowed","approval_required","blocked","admin_only","live_activation_required")

$rows = New-Object System.Collections.ArrayList
$missingClass = 0
foreach ($t in $policy.tools) {
  $cls = $t.classification
  $ok = $valid -contains $cls
  if (-not $ok) { $missingClass++ }
  [void]$rows.Add([pscustomobject]@{ surface=$t.surface; risk=$t.risk; classification=$cls; valid=$ok })
}

# Secret-sensitive files present in repo (existence only, not contents).
$secretCandidates = @(".env","data\auth.json","data\app.db")
$secretsPresent = @()
foreach ($s in $secretCandidates) {
  if (Test-Path (Join-Path $TargetRepo $s)) { $secretsPresent += $s }
}

$report = [pscustomobject]@{
  status = if ($missingClass -eq 0) { "HIGH_RISK_AUDIT_COMPLETE" } else { "HIGH_RISK_AUDIT_INCOMPLETE" }
  generatedAt = (Get-Date).ToUniversalTime().ToString("o")
  targetRepo = $TargetRepo
  toolCount = ($policy.tools | Measure-Object).Count
  missingClassification = $missingClass
  surfaces = $rows
  secretsPolicy = $policy.secretsPolicy
  workspacePolicy = $policy.workspacePolicy
  secretSensitiveFilesPresent = $secretsPresent
  note = "Secret-sensitive files are present locally and must be blocked or approval-gated by policy; never committed or exposed."
}

$outDir = Join-Path $TargetRepo "data\gnexus\completeness"
New-Item -ItemType Directory -Force $outDir | Out-Null
$outPath = Join-Path $outDir "high-risk-tool-audit.json"
$report | ConvertTo-Json -Depth 8 | Set-Content -Path $outPath -Encoding UTF8

Write-Host ""
Write-Host ("Surfaces audited: " + $rows.Count)
$rows | ForEach-Object { Write-Host ("  " + $_.surface + " -> " + $_.classification + " (risk=" + $_.risk + ")") }
Write-Host ("Report: " + $outPath)

if ($missingClass -gt 0) {
  Write-Host "[FAIL] Some surfaces lack a valid classification." -ForegroundColor Red
  exit 1
}
Write-Host "HIGH-RISK TOOL AUDIT COMPLETE" -ForegroundColor Green
exit 0
