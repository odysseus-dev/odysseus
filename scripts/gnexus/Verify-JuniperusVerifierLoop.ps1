#Requires -Version 5.1
param(
  [string]$TargetRepo = "",
  [string]$WorkspaceRoot = "C:\Users\iamcy\CymaticsDev"
)
$ErrorActionPreference = "Stop"
Write-Host "============================================================"
Write-Host "JUNIPERUS070 v0.1.0 - VERIFY"
Write-Host "============================================================"
function Resolve-ScriptRoot {
  if ($PSScriptRoot) { return $PSScriptRoot }
  if ($PSCommandPath) { return (Split-Path -Parent $PSCommandPath) }
  if ($MyInvocation.MyCommand.Path) { return (Split-Path -Parent $MyInvocation.MyCommand.Path) }
  return (Get-Location).Path
}
function Resolve-PackageRoot {
  $scriptDir = Resolve-ScriptRoot
  $candidate = Split-Path -Parent $scriptDir
  if (Test-Path (Join-Path $candidate "payload")) { return $candidate }
  $parent = Split-Path -Parent $candidate
  if ($parent -and (Test-Path (Join-Path $parent "payload"))) { return $parent }
  if (Test-Path (Join-Path (Get-Location).Path "payload")) { return (Get-Location).Path }
  return $candidate
}
function Resolve-TargetRepo([string]$Given) {
  $candidates = New-Object System.Collections.ArrayList
  if ($Given -and $Given.Trim().Length -gt 0) { [void]$candidates.Add($Given) }
  [void]$candidates.Add("C:\Users\iamcy\CymaticsDev\00_SYSTEMS\Juniperus")
  [void]$candidates.Add("C:\Users\iamcy\CymaticsDev\01_ACTIVE_REPOS\Juniperus")
  [void]$candidates.Add("C:\Users\iamcy\Juniperus")
  [void]$candidates.Add((Join-Path (Get-Location).Path "Juniperus"))
  [void]$candidates.Add((Get-Location).Path)
  foreach ($c in $candidates) {
    if ($c -and (Test-Path (Join-Path $c "app.py")) -and (Test-Path (Join-Path $c "launch-windows.ps1"))) { return (Resolve-Path $c).Path }
  }
  return $Given
}
function Add-Check([string]$Name, [bool]$Pass, [string]$Detail) {
  $null = $script:checks.Add([pscustomobject]@{ name=$Name; pass=$Pass; detail=$Detail })
  if ($Pass) { Write-Host ("[OK] " + $Name) -ForegroundColor Green } else { Write-Host ("[FAIL] " + $Name + " :: " + $Detail) -ForegroundColor Red }
}
function Test-JsonFile([string]$Path) { try { Get-Content -Raw -Path $Path | ConvertFrom-Json | Out-Null; return $true } catch { return $false } }
$PackageRoot = Resolve-PackageRoot
$Repo = Resolve-TargetRepo $TargetRepo
$script:checks = New-Object System.Collections.ArrayList
Write-Host ("PackageRoot : " + $PackageRoot)
Write-Host ("TargetRepo  : " + $Repo)
Write-Host ("Workspace   : " + $WorkspaceRoot)
Add-Check "Package root exists" (Test-Path $PackageRoot) $PackageRoot
Add-Check "Payload root exists" (Test-Path (Join-Path $PackageRoot "payload")) (Join-Path $PackageRoot "payload")
Add-Check "Target repo resolved" ((Test-Path $Repo) -and (Test-Path (Join-Path $Repo "app.py"))) $Repo
$required = @(
  "docs\JUNIPERUS_VERIFIER_REPAIR_ROLLBACK_LOOP.md",
  "config\gnexus.verifier-loop-policy.example.json",
  "src\gnexus_governance\verifier_loop.py",
  "routes\gnexus_verifier_loop_routes.py",
  "static\gnexus\verifier-loop.html",
  "scripts\gnexus\Verify-JuniperusVerifierLoop.ps1",
  "patches\JUNIPERUS070_VERIFIER_LOOP_ROUTE_BINDING.patch",
  "data\gnexus\verifier-loop\verification-queue.json",
  "data\gnexus\verifier-loop\verification-results.json",
  "data\gnexus\verifier-loop\repair-queue.json",
  "data\gnexus\verifier-loop\rollback-requests.json",
  "data\gnexus\verifier-loop\verifier-runbook.json",
  "data\gnexus\mission-control\verifier-loop-state.json",
  "data\gnexus\receipts\JUNIPERUS070-closeout.json"
)
foreach ($rel in $required) { $p = Join-Path $Repo $rel; Add-Check ("Required file " + $rel) (Test-Path $p) $p }
$app = Join-Path $Repo "app.py"
$appText = if (Test-Path $app) { Get-Content -Raw -Path $app } else { "" }
Add-Check "app.py verifier loop route bound" ($appText -like "*setup_gnexus_verifier_loop_routes*") "setup_gnexus_verifier_loop_routes"
Add-Check "app.py verifier loop marker present" (($appText -like "*JUNIPERUS070*") -or ($appText -like "*Gnexus verifier loop routes initialized*")) "JUNIPERUS070 marker"
$jsonFiles = @(
  "config\gnexus.verifier-loop-policy.example.json",
  "data\gnexus\verifier-loop\verification-queue.json",
  "data\gnexus\verifier-loop\verification-results.json",
  "data\gnexus\verifier-loop\repair-queue.json",
  "data\gnexus\verifier-loop\rollback-requests.json",
  "data\gnexus\verifier-loop\verifier-runbook.json",
  "data\gnexus\mission-control\verifier-loop-state.json",
  "data\gnexus\receipts\JUNIPERUS070-closeout.json"
)
foreach ($rel in $jsonFiles) { $p = Join-Path $Repo $rel; Add-Check ("JSON valid " + $rel) ((Test-Path $p) -and (Test-JsonFile $p)) $p }
$modulePath = Join-Path $Repo "src\gnexus_governance\verifier_loop.py"
$moduleText = if (Test-Path $modulePath) { Get-Content -Raw -Path $modulePath } else { "" }
Add-Check "verifier loop creates verification requests" ($moduleText -like "*create_verification_request*") "create_verification_request"
Add-Check "verifier loop records repair items" ($moduleText -like "*create_repair_item*") "create_repair_item"
Add-Check "verifier loop records rollback requests" ($moduleText -like "*create_rollback_request*") "create_rollback_request"
Add-Check "verifier loop does not execute shell" (($moduleText -notlike "*subprocess*") -and ($moduleText -notlike "*os.system*")) "no shell execution"
$pyFiles = @("src\gnexus_governance\verifier_loop.py", "routes\gnexus_verifier_loop_routes.py")
foreach ($rel in $pyFiles) {
  $p = Join-Path $Repo $rel
  $ok = $false
  if (Test-Path $p) {
    try {
      $proc = Start-Process -FilePath "python" -ArgumentList @("-m", "py_compile", $p) -NoNewWindow -Wait -PassThru -RedirectStandardOutput ([System.IO.Path]::GetTempFileName()) -RedirectStandardError ([System.IO.Path]::GetTempFileName()) -ErrorAction SilentlyContinue
      if ($proc -and $proc.ExitCode -eq 0) { $ok = $true }
    } catch { $ok = $false }
  }
  Add-Check ("Python compile " + $rel) $ok $p
}
$gnexusCmd = Get-ChildItem -Path $Repo -Recurse -Filter "*.cmd" -ErrorAction SilentlyContinue | Where-Object { $_.FullName -like "*gnexus*" }
Add-Check "No new Gnexus .cmd files" (($gnexusCmd | Measure-Object).Count -eq 0) ""
$legacyHit = $false
$scanFiles = @("docs\JUNIPERUS_VERIFIER_REPAIR_ROLLBACK_LOOP.md", "config\gnexus.verifier-loop-policy.example.json", "src\gnexus_governance\verifier_loop.py", "routes\gnexus_verifier_loop_routes.py", "static\gnexus\verifier-loop.html")
foreach ($rel in $scanFiles) { $p = Join-Path $Repo $rel; if (Test-Path $p) { $t = Get-Content -Raw -Path $p; $badRoot = "C:" + "\CymaticsDev"; if ($t -like ("*" + $badRoot + "*")) { $legacyHit = $true } } }
Add-Check "No legacy wrong root literal in installed verifier loop files" (-not $legacyHit) ""
$failed = @($checks | Where-Object { -not $_.pass })
$reportRoot = Join-Path $PackageRoot ".gnexus\juniperus070"
New-Item -ItemType Directory -Force -Path $reportRoot | Out-Null
$report = [pscustomobject]@{
  status = if ($failed.Count -eq 0) { "JUNIPERUS_VERIFIER_REPAIR_ROLLBACK_LOOP_READY_LOCAL_CLOSEOUT" } else { "REPAIR_REQUIRED" }
  generatedAt = (Get-Date).ToUniversalTime().ToString("o")
  packageRoot = $PackageRoot
  targetRepo = $Repo
  workspaceRoot = $WorkspaceRoot
  checks = $checks
  failedCount = $failed.Count
  restartRequired = $true
  verifierLoopUrl = "http://127.0.0.1:7010/gnexus/verifier-loop"
}
$reportPath = Join-Path $reportRoot "verification-report.json"
$report | ConvertTo-Json -Depth 8 | Set-Content -Path $reportPath -Encoding UTF8
Write-Host ""
Write-Host ("Report: " + $reportPath)
if ($failed.Count -gt 0) { Write-Host "JUNIPERUS070 VERIFY FAILED" -ForegroundColor Red; exit 1 }
Write-Host "JUNIPERUS070 VERIFY PASSED" -ForegroundColor Green
exit 0
