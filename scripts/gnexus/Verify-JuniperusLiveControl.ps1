#Requires -Version 5.1
param(
  [string]$TargetRepo = "C:\Users\iamcy\CymaticsDev\00_SYSTEMS\Juniperus",
  [string]$WorkspaceRoot = "C:\Users\iamcy\CymaticsDev"
)
$ErrorActionPreference = "Stop"
$scriptPath = if ($PSCommandPath) { $PSCommandPath } elseif ($MyInvocation.MyCommand.Path) { $MyInvocation.MyCommand.Path } else { $null }
if (-not $scriptPath) { throw "Cannot resolve script path." }
$checks = New-Object System.Collections.ArrayList
function Add-Check([string]$Name, [bool]$Pass, [string]$Detail="") {
  [void]$checks.Add([pscustomobject]@{ name=$Name; pass=$Pass; detail=$Detail })
  if ($Pass) { Write-Host "[OK] $Name" -ForegroundColor Green } else { Write-Host "[FAIL] $Name :: $Detail" -ForegroundColor Red }
}
Add-Check "Target repo exists" (Test-Path $TargetRepo) $TargetRepo
Add-Check "Workspace root exists" (Test-Path $WorkspaceRoot) $WorkspaceRoot
$required = @(
  "docs\JUNIPERUS_CONTROLLED_WRITE_LIVE_ACTIVATION_FINALIZER.md",
  "docs\JUNIPERUS_CONTROLLED_WRITE_LIVE_ACTIVATION_RUNBOOK.md",
  "config\gnexus.live-control-policy.example.json",
  "src\gnexus_governance\live_control.py",
  "routes\gnexus_live_control_routes.py",
  "static\gnexus\live-control.html",
  "data\gnexus\live-control\activation-gates.json",
  "data\gnexus\live-control\authority-matrix.json",
  "data\gnexus\live-control\readiness-checklist.json",
  "data\gnexus\live-control\finalizer-ledger.json",
  "data\gnexus\mission-control\live-control-state.json",
  "data\gnexus\receipts\JUNIPERUS100-closeout.json"
)
foreach ($rel in $required) {
  $p = Join-Path $TargetRepo $rel
  Add-Check "Required file $rel" (Test-Path $p) $p
}
$appPy = Join-Path $TargetRepo "app.py"
$appText = if (Test-Path $appPy) { Get-Content $appPy -Raw } else { "" }
Add-Check "app.py live-control route bound" ($appText -match "setup_gnexus_live_control_routes") "setup_gnexus_live_control_routes"

$jsons = @(
  "config\gnexus.live-control-policy.example.json",
  "data\gnexus\live-control\activation-gates.json",
  "data\gnexus\live-control\authority-matrix.json",
  "data\gnexus\live-control\readiness-checklist.json",
  "data\gnexus\live-control\finalizer-ledger.json",
  "data\gnexus\mission-control\live-control-state.json",
  "data\gnexus\receipts\JUNIPERUS100-closeout.json"
)
foreach ($rel in $jsons) {
  $p = Join-Path $TargetRepo $rel
  try {
    if (Test-Path $p) {
      $obj = Get-Content $p -Raw | ConvertFrom-Json
      Add-Check "JSON valid $rel" $true $p
    } else {
      Add-Check "JSON valid $rel" $false "missing"
    }
  } catch {
    Add-Check "JSON valid $rel" $false $_.Exception.Message
  }
}

# Upstream state files expected from 010-090.
$upstream = @(
  "data\gnexus\mission-control\governance-state.json",
  "data\gnexus\mission-control\app-dock-state.json",
  "data\gnexus\mission-control\approval-desk-state.json",
  "data\gnexus\mission-control\interceptor-state.json",
  "data\gnexus\mission-control\diff-gate-state.json",
  "data\gnexus\mission-control\patch-apply-state.json",
  "data\gnexus\mission-control\verifier-loop-state.json",
  "data\gnexus\mission-control\operator-loop-state.json",
  "data\gnexus\mission-control\memory-routing-state.json"
)
foreach ($rel in $upstream) {
  $p = Join-Path $TargetRepo $rel
  Add-Check "Upstream state present $rel" (Test-Path $p) $p
}

try {
  $gates = Get-Content (Join-Path $TargetRepo "data\gnexus\live-control\activation-gates.json") -Raw | ConvertFrom-Json
  Add-Check "Live activation disabled" (-not [bool]$gates.liveActivationEnabled) "liveActivationEnabled"
  Add-Check "External writes disabled" (-not [bool]$gates.externalWritesEnabled) "externalWritesEnabled"
  Add-Check "Connector calls disabled" (-not [bool]$gates.connectorCallsEnabled) "connectorCallsEnabled"
  Add-Check "Human approval required" ([bool]$gates.requireHumanApproval) "requireHumanApproval"
} catch {
  Add-Check "Activation gate boundary readable" $false $_.Exception.Message
}

$py = Join-Path $TargetRepo "venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) { $py = $cmd.Source }
}
if ($py -and (Test-Path $py)) {
  $pyFiles = @(
    "src\gnexus_governance\live_control.py",
    "routes\gnexus_live_control_routes.py"
  )
  foreach ($rel in $pyFiles) {
    $p = Join-Path $TargetRepo $rel
    if (Test-Path $p) {
      & $py -m py_compile $p
      Add-Check "Python compile $rel" ($LASTEXITCODE -eq 0) $p
    }
  }
} else {
  Add-Check "Python available for compile" $false "python not found"
}

$installedFiles = @(
  "docs\JUNIPERUS_CONTROLLED_WRITE_LIVE_ACTIVATION_FINALIZER.md",
  "docs\JUNIPERUS_CONTROLLED_WRITE_LIVE_ACTIVATION_RUNBOOK.md",
  "config\gnexus.live-control-policy.example.json",
  "src\gnexus_governance\live_control.py",
  "routes\gnexus_live_control_routes.py",
  "static\gnexus\live-control.html"
)
$badRoot = $false
foreach ($rel in $installedFiles) {
  $p = Join-Path $TargetRepo $rel
  if (Test-Path $p) {
    $txt = Get-Content $p -Raw
    if ($txt -match [regex]::Escape("C:\CymaticsDev")) { $badRoot = $true }
  }
}
Add-Check "No legacy wrong-root literal in finalizer files" (-not $badRoot) "C:\CymaticsDev"

$cmdFiles = Get-ChildItem -Path (Join-Path $TargetRepo "data\gnexus"),(Join-Path $TargetRepo "scripts\gnexus") -Filter *.cmd -Recurse -ErrorAction SilentlyContinue
Add-Check "No new Gnexus .cmd files" (($cmdFiles | Measure-Object).Count -eq 0) ""

$failed = @($checks | Where-Object { -not $_.pass })
$status = if ($failed.Count -eq 0) { "JUNIPERUS_CONTROLLED_WRITE_LIVE_ACTIVATION_FINALIZER_READY_LOCAL_CLOSEOUT" } else { "REPAIR_REQUIRED" }
$report = [pscustomobject]@{
  status = $status
  generatedAt = (Get-Date).ToUniversalTime().ToString("o")
  targetRepo = $TargetRepo
  workspaceRoot = $WorkspaceRoot
  checks = $checks
  failedCount = $failed.Count
  liveControlUrl = "http://127.0.0.1:7010/gnexus/live-control"
}
$reportDir = Join-Path $TargetRepo "data\gnexus\verification"
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
$reportPath = Join-Path $reportDir "JUNIPERUS100-verification-report.json"
$report | ConvertTo-Json -Depth 12 | Set-Content -Encoding UTF8 $reportPath
Write-Host ""
Write-Host "Report: $reportPath"
if ($failed.Count -eq 0) {
  Write-Host "JUNIPERUS100 VERIFY PASSED" -ForegroundColor Green
  exit 0
}
Write-Host "JUNIPERUS100 VERIFY FAILED" -ForegroundColor Red
exit 1
