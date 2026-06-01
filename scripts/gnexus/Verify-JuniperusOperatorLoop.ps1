#Requires -Version 5.1
param(
  [string]$TargetRepo = "",
  [string]$WorkspaceRoot = "C:\Users\iamcy\CymaticsDev"
)
$ErrorActionPreference = "Stop"
function Resolve-TargetRepo {
  param([string]$Given)
  if ($Given -and (Test-Path (Join-Path $Given "app.py"))) { return (Resolve-Path $Given).Path }
  $candidates = @(
    "C:\Users\iamcy\CymaticsDev\00_SYSTEMS\Juniperus",
    "C:\Users\iamcy\CymaticsDev\01_ACTIVE_REPOS\Juniperus",
    "C:\Users\iamcy\Juniperus"
  )
  foreach ($c in $candidates) {
    if (Test-Path (Join-Path $c "app.py")) { return (Resolve-Path $c).Path }
  }
  throw "Could not resolve Juniperus target repo."
}
$TargetRepo = Resolve-TargetRepo $TargetRepo
$checks = New-Object System.Collections.ArrayList
function Add-Check([string]$Name, [bool]$Pass, [string]$Detail = "") {
  [void]$checks.Add([pscustomobject]@{ name=$Name; pass=$Pass; detail=$Detail })
  if ($Pass) { Write-Host "[OK] $Name" -ForegroundColor Green }
  else { Write-Host "[FAIL] $Name :: $Detail" -ForegroundColor Red }
}
$required = @(
  "docs\JUNIPERUS_FULL_OPERATOR_LOOP.md",
  "config\gnexus.operator-loop-policy.example.json",
  "src\gnexus_governance\operator_loop.py",
  "routes\gnexus_operator_loop_routes.py",
  "static\gnexus\operator-loop.html",
  "data\gnexus\operator-loop\operator-queue.json",
  "data\gnexus\operator-loop\operation-ledger.json",
  "data\gnexus\operator-loop\operator-runbook.json",
  "data\gnexus\mission-control\operator-loop-state.json",
  "data\gnexus\receipts\JUNIPERUS080-closeout.json",
  "patches\JUNIPERUS080_OPERATOR_LOOP_ROUTE_BINDING.patch"
)
foreach ($r in $required) {
  $p = Join-Path $TargetRepo $r
  Add-Check "Required file $r" (Test-Path $p) $p
}
$app = Get-Content (Join-Path $TargetRepo "app.py") -Raw
Add-Check "app.py operator loop route bound" ($app -like "*setup_gnexus_operator_loop_routes*") "setup_gnexus_operator_loop_routes"
Add-Check "app.py JUNIPERUS080 marker present" ($app -like "*JUNIPERUS080 OPERATOR LOOP ROUTE BINDING*") "route marker"

$jsonFiles = @(
  "config\gnexus.operator-loop-policy.example.json",
  "data\gnexus\operator-loop\operator-queue.json",
  "data\gnexus\operator-loop\operation-ledger.json",
  "data\gnexus\operator-loop\operator-runbook.json",
  "data\gnexus\mission-control\operator-loop-state.json",
  "data\gnexus\receipts\JUNIPERUS080-closeout.json"
)
foreach ($j in $jsonFiles) {
  $p = Join-Path $TargetRepo $j
  try { $null = Get-Content $p -Raw | ConvertFrom-Json; Add-Check "JSON valid $j" $true $p }
  catch { Add-Check "JSON valid $j" $false $_.Exception.Message }
}

$py = Get-Command python -ErrorAction SilentlyContinue
if ($py) {
  $files = @(
    (Join-Path $TargetRepo "src\gnexus_governance\operator_loop.py"),
    (Join-Path $TargetRepo "routes\gnexus_operator_loop_routes.py")
  )
  foreach ($f in $files) {
    & $py.Source -m py_compile $f
    Add-Check "Python compile $(Split-Path $f -Leaf)" ($LASTEXITCODE -eq 0) $f
  }
} else {
  Add-Check "Python available for compile check" $false "python not on PATH"
}

$newCmd = Get-ChildItem (Join-Path $TargetRepo "scripts\gnexus") -Recurse -Filter "*.cmd" -ErrorAction SilentlyContinue
Add-Check "No new Gnexus .cmd files" (($newCmd | Measure-Object).Count -eq 0) ""

$badLiteral = @()
foreach ($rel in $required) {
  $p = Join-Path $TargetRepo $rel
  if (Test-Path $p) {
    $txt = Get-Content $p -Raw -ErrorAction SilentlyContinue
    if ($txt -like "*C:\CymaticsDev*") { $badLiteral += $rel }
  }
}
Add-Check "No legacy C:\CymaticsDev literal in installed operator-loop files" ($badLiteral.Count -eq 0) ($badLiteral -join ", ")

$failed = @($checks | Where-Object { -not $_.pass })
$report = [pscustomobject]@{
  status = if ($failed.Count -eq 0) { "JUNIPERUS_FULL_OPERATOR_LOOP_READY_LOCAL_CLOSEOUT" } else { "REPAIR_REQUIRED" }
  generatedAt = (Get-Date).ToUniversalTime().ToString("o")
  targetRepo = $TargetRepo
  workspaceRoot = $WorkspaceRoot
  checks = $checks
  failedCount = $failed.Count
  operatorLoopUrl = "http://127.0.0.1:7010/gnexus/operator-loop"
}
$reportRoot = Join-Path $env:TEMP "juniperus080-report"
if ($PSScriptRoot) {
  $pkg = Split-Path -Parent $PSScriptRoot
  $reportRoot = Join-Path $pkg ".gnexus\juniperus080"
}
New-Item -ItemType Directory -Force $reportRoot | Out-Null
$reportPath = Join-Path $reportRoot "verification-report.json"
$report | ConvertTo-Json -Depth 8 | Set-Content -Path $reportPath -Encoding UTF8
Write-Host ""
Write-Host "Report: $reportPath"
if ($failed.Count -gt 0) {
  Write-Host "JUNIPERUS080 VERIFY FAILED" -ForegroundColor Red
  exit 1
}
Write-Host "JUNIPERUS080 VERIFY PASSED" -ForegroundColor Green
exit 0
