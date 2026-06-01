#Requires -Version 5.1
param(
  [string]$TargetRepo = "C:\Users\iamcy\CymaticsDev\00_SYSTEMS\Juniperus"
)
$ErrorActionPreference = "Stop"
Write-Host "Juniperus Diff Gate repo verifier"
$required = @(
  "src\gnexus_governance\diff_gate.py",
  "routes\gnexus_diff_gate_routes.py",
  "static\gnexus\diff-gate.html",
  "data\gnexus\diff-gate\patch-queue.json"
)
$failed = 0
foreach ($r in $required) {
  $p = Join-Path $TargetRepo $r
  if (Test-Path $p) { Write-Host "[OK] $r" -ForegroundColor Green } else { Write-Host "[FAIL] $r" -ForegroundColor Red; $failed++ }
}
if ($failed -gt 0) { exit 1 }
exit 0
