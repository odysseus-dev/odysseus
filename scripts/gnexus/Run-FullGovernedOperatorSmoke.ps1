#Requires -Version 5.1
<#
  Full governed operation smoke test for Juniperus / Gnexus Operations Console.

  Proves diff gate -> approval object -> rollback snapshot -> patch apply ->
  verify -> rollback -> verify -> receipt, inside a sandbox folder. Local-first.
  No shell execution against the real workspace; no cloud calls.

  Usage:
    powershell -ExecutionPolicy Bypass -File .\scripts\gnexus\Run-FullGovernedOperatorSmoke.ps1
#>
param(
  [string]$TargetRepo = "",
  [switch]$NoApprove
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

$venvPy = Join-Path $TargetRepo "venv\Scripts\python.exe"
$py = if (Test-Path $venvPy) { $venvPy } else { (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $py) { throw "Python not found (no venv, none on PATH)." }
Write-Host ("Python: " + $py)

$proof = Join-Path $TargetRepo "scripts\gnexus\run_governed_operator_proof.py"
if (-not (Test-Path $proof)) { throw "Proof script missing: $proof" }

$args = @($proof)
if (-not $NoApprove) { $args += "--approve" }

Write-Host ""
Write-Host "==> Running governed operation proof"
& $py @args
$code = $LASTEXITCODE

$receipt = Join-Path $TargetRepo "data\gnexus\operator-loop\sandbox\proof-receipt.json"
if (-not (Test-Path $receipt)) {
  Write-Host "[FAIL] proof receipt was not written" -ForegroundColor Red
  exit 1
}

$obj = Get-Content $receipt -Raw | ConvertFrom-Json
Write-Host ""
Write-Host ("Proof status: " + $obj.status)

$ok = $true
if (-not $NoApprove) {
  if (-not $obj.diffGateProven) { Write-Host "[FAIL] diff gate not proven" -ForegroundColor Red; $ok = $false }
  if (-not $obj.approvalObjectExists) { Write-Host "[FAIL] approval object missing" -ForegroundColor Red; $ok = $false }
  if (-not $obj.rollbackSnapshotProven) { Write-Host "[FAIL] rollback snapshot not proven" -ForegroundColor Red; $ok = $false }
  if (-not $obj.applyVerified) { Write-Host "[FAIL] apply not verified" -ForegroundColor Red; $ok = $false }
  if (-not $obj.rollbackVerified) { Write-Host "[FAIL] rollback not verified" -ForegroundColor Red; $ok = $false }
}

if ($ok -and $code -eq 0) {
  Write-Host "GOVERNED OPERATION SMOKE PASSED" -ForegroundColor Green
  exit 0
}
Write-Host "GOVERNED OPERATION SMOKE FAILED" -ForegroundColor Red
exit 1
