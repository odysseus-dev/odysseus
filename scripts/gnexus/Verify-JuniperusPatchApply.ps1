#Requires -Version 5.1
param(
  [string]$TargetRepo = "C:\Users\iamcy\CymaticsDev\00_SYSTEMS\Juniperus",
  [string]$WorkspaceRoot = "C:\Users\iamcy\CymaticsDev"
)
$ErrorActionPreference = "Stop"
$scriptPath = if ($PSCommandPath) { $PSCommandPath } elseif ($MyInvocation.MyCommand.Path) { $MyInvocation.MyCommand.Path } else { $null }
if ($scriptPath) { $here = Split-Path -Parent $scriptPath } elseif ($PSScriptRoot) { $here = $PSScriptRoot } else { $here = (Get-Location).Path }
$repo = $TargetRepo
$checks = New-Object System.Collections.ArrayList
function Add-Check([string]$Name, [bool]$Pass, [string]$Detail) {
  $null = $checks.Add([pscustomobject]@{ name=$Name; pass=$Pass; detail=$Detail })
  if ($Pass) { Write-Host ("[OK] " + $Name) -ForegroundColor Green } else { Write-Host ("[FAIL] " + $Name + " :: " + $Detail) -ForegroundColor Red }
}
function Test-JsonFile([string]$Path) {
  try { Get-Content -Raw -Path $Path | ConvertFrom-Json | Out-Null; return $true } catch { return $false }
}
Add-Check "Target repo exists" (Test-Path $repo) $repo
Add-Check "Target repo has app.py" (Test-Path (Join-Path $repo "app.py")) "app.py"
$required = @(
  "docs\JUNIPERUS_APPROVED_PATCH_APPLY_ROLLBACK_EXECUTOR.md",
  "config\gnexus.patch-apply-policy.example.json",
  "src\gnexus_governance\patch_apply.py",
  "routes\gnexus_patch_apply_routes.py",
  "static\gnexus\patch-apply.html",
  "data\gnexus\patch-apply\apply-ledger.json",
  "data\gnexus\patch-apply\rollback-snapshots.json",
  "data\gnexus\mission-control\patch-apply-state.json",
  "data\gnexus\receipts\JUNIPERUS060-closeout.json",
  "scripts\gnexus\Verify-JuniperusPatchApply.ps1"
)
foreach ($rel in $required) {
  $p = Join-Path $repo $rel
  Add-Check ("Required file " + $rel) (Test-Path $p) $p
}
$app = Join-Path $repo "app.py"
$appText = if (Test-Path $app) { Get-Content -Raw -Path $app } else { "" }
Add-Check "app.py patch apply route bound" ($appText -like "*setup_gnexus_patch_apply_routes*") "setup_gnexus_patch_apply_routes"
Add-Check "app.py patch apply marker present" ($appText -like "*JUNIPERUS060*" -or $appText -like "*Gnexus patch apply routes initialized*") "JUNIPERUS060 route marker"
$jsonFiles = @(
  "config\gnexus.patch-apply-policy.example.json",
  "data\gnexus\patch-apply\apply-ledger.json",
  "data\gnexus\patch-apply\rollback-snapshots.json",
  "data\gnexus\mission-control\patch-apply-state.json",
  "data\gnexus\receipts\JUNIPERUS060-closeout.json"
)
foreach ($rel in $jsonFiles) {
  $p = Join-Path $repo $rel
  Add-Check ("JSON valid " + $rel) ((Test-Path $p) -and (Test-JsonFile $p)) $p
}
$pyFiles = @("src\gnexus_governance\patch_apply.py", "routes\gnexus_patch_apply_routes.py")
foreach ($rel in $pyFiles) {
  $p = Join-Path $repo $rel
  $ok = $false
  if (Test-Path $p) {
    $code = @"
import py_compile
py_compile.compile(r'$p', doraise=True)
"@
    $tmp = [System.IO.Path]::GetTempFileName() + ".py"
    Set-Content -Path $tmp -Value $code -Encoding UTF8
    $proc = Start-Process -FilePath "python" -ArgumentList @($tmp) -NoNewWindow -Wait -PassThru -RedirectStandardOutput ($tmp + ".out") -RedirectStandardError ($tmp + ".err") -ErrorAction SilentlyContinue
    if ($proc -and $proc.ExitCode -eq 0) { $ok = $true }
    Remove-Item $tmp,($tmp + ".out"),($tmp + ".err") -ErrorAction SilentlyContinue
  }
  Add-Check ("Python compile " + $rel) $ok $p
}
$gnexusCmd = Get-ChildItem -Path $repo -Recurse -Filter "*.cmd" -ErrorAction SilentlyContinue | Where-Object { $_.FullName -like "*gnexus*" }
Add-Check "No new Gnexus .cmd files" (($gnexusCmd | Measure-Object).Count -eq 0) ""
$legacyHit = $false
$scanFiles = @(
  "docs\JUNIPERUS_APPROVED_PATCH_APPLY_ROLLBACK_EXECUTOR.md",
  "config\gnexus.patch-apply-policy.example.json",
  "src\gnexus_governance\patch_apply.py",
  "routes\gnexus_patch_apply_routes.py",
  "static\gnexus\patch-apply.html"
)
foreach ($rel in $scanFiles) {
  $p = Join-Path $repo $rel
  if (Test-Path $p) {
    $t = Get-Content -Raw -Path $p
    if ($t -like "*C:\CymaticsDev*") { $legacyHit = $true }
  }
}
Add-Check "No legacy C:\CymaticsDev literal in patch apply files" (-not $legacyHit) ""
$failed = @($checks | Where-Object { -not $_.pass })
$reportRoot = Join-Path (Split-Path -Parent $here) ".gnexus\juniperus060"
New-Item -ItemType Directory -Force -Path $reportRoot | Out-Null
$report = [pscustomobject]@{
  status = if ($failed.Count -eq 0) { "JUNIPERUS_APPROVED_PATCH_APPLY_ROLLBACK_EXECUTOR_READY_LOCAL_CLOSEOUT" } else { "REPAIR_REQUIRED" }
  generatedAt = (Get-Date).ToUniversalTime().ToString("o")
  targetRepo = $repo
  workspaceRoot = $WorkspaceRoot
  checks = $checks
  failedCount = $failed.Count
  restartRequired = $true
  patchApplyUrl = "http://127.0.0.1:7010/gnexus/patch-apply"
}
$reportPath = Join-Path $reportRoot "verification-report.json"
$report | ConvertTo-Json -Depth 8 | Set-Content -Path $reportPath -Encoding UTF8
Write-Host ""
Write-Host ("Report: " + $reportPath)
if ($failed.Count -gt 0) {
  Write-Host "JUNIPERUS060 VERIFY FAILED" -ForegroundColor Red
  exit 1
}
Write-Host "JUNIPERUS060 VERIFY PASSED" -ForegroundColor Green
exit 0
