#Requires -Version 5.1
param(
  [string]$TargetRepo = "",
  [string]$WorkspaceRoot = "C:\Users\iamcy\CymaticsDev"
)

$scriptPath = if ($PSCommandPath) { $PSCommandPath } else { $MyInvocation.MyCommand.Path }
$repo = if ($TargetRepo) { $TargetRepo } else { Split-Path -Parent (Split-Path -Parent $scriptPath) }
if (-not (Test-Path (Join-Path $repo "app.py"))) {
  $repo = "C:\Users\iamcy\CymaticsDev\00_SYSTEMS\Juniperus"
}

$checks = @()
function Add-Check($Name, $Pass, $Detail="") {
  $script:checks += [ordered]@{ name=$Name; pass=[bool]$Pass; detail=$Detail }
  if ($Pass) { Write-Host "[OK] $Name" -ForegroundColor Green } else { Write-Host "[FAIL] $Name :: $Detail" -ForegroundColor Red }
}

Write-Host "============================================================"
Write-Host "Verify-JuniperusMemoryRouting"
Write-Host "============================================================"
Write-Host "Repo: $repo"

Add-Check "Repo has app.py" (Test-Path (Join-Path $repo "app.py")) $repo
Add-Check "Memory routing module exists" (Test-Path (Join-Path $repo "src\gnexus_governance\memory_routing.py")) ""
Add-Check "Memory routing route exists" (Test-Path (Join-Path $repo "routes\gnexus_memory_routing_routes.py")) ""
Add-Check "Memory routing page exists" (Test-Path (Join-Path $repo "static\gnexus\memory-routing.html")) ""
Add-Check "Memory routing state exists" (Test-Path (Join-Path $repo "data\gnexus\mission-control\memory-routing-state.json")) ""
$app = Get-Content -Raw -Path (Join-Path $repo "app.py")
Add-Check "app.py bound to memory routing route" ($app -match "setup_gnexus_memory_routing_routes") ""

$failed = @($checks | Where-Object { -not $_.pass })
if ($failed.Count -gt 0) { exit 1 }
exit 0
