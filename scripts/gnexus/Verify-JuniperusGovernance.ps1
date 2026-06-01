#Requires -Version 5.1
param(
  [string]$TargetRepo = "",
  [string]$WorkspaceRoot = "C:\Users\iamcy\CymaticsDev"
)
$ErrorActionPreference = "Stop"
if(-not $TargetRepo){ $TargetRepo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }
$checks = New-Object System.Collections.ArrayList
function Add-Check($name,$pass,$detail){ [void]$checks.Add([ordered]@{ name=$name; pass=[bool]$pass; detail=$detail }) }
Add-Check "Target repo exists" (Test-Path $TargetRepo) $TargetRepo
Add-Check "app.py exists" (Test-Path (Join-Path $TargetRepo "app.py")) "app.py"
Add-Check "governance route exists" (Test-Path (Join-Path $TargetRepo "routes\gnexus_governance_routes.py")) "routes\gnexus_governance_routes.py"
Add-Check "governance module exists" (Test-Path (Join-Path $TargetRepo "src\gnexus_governance\policy.py")) "src\gnexus_governance\policy.py"
Add-Check "workspace config exists" (Test-Path (Join-Path $TargetRepo "config\gnexus.workspace.example.json")) "config\gnexus.workspace.example.json"
Add-Check "policy config exists" (Test-Path (Join-Path $TargetRepo "config\gnexus.policy.example.json")) "config\gnexus.policy.example.json"
$appPy = Join-Path $TargetRepo "app.py"
$routeBound = $false
if(Test-Path $appPy){ $routeBound = ((Get-Content $appPy -Raw) -match "setup_gnexus_governance_routes") }
Add-Check "app.py governance route binding" $routeBound "setup_gnexus_governance_routes"
Add-Check "project registry exists" (Test-Path (Join-Path $TargetRepo "data\gnexus\project-registry.json")) "data\gnexus\project-registry.json"
Add-Check "app registry exists" (Test-Path (Join-Path $TargetRepo "data\gnexus\app-registry.json")) "data\gnexus\app-registry.json"
Add-Check "approval queue exists" (Test-Path (Join-Path $TargetRepo "data\gnexus\approval-queue.json")) "data\gnexus\approval-queue.json"
Add-Check "operation receipts exists" (Test-Path (Join-Path $TargetRepo "data\gnexus\operation-receipts.json")) "data\gnexus\operation-receipts.json"
$oldPathHits = @()
foreach($f in @("config\gnexus.workspace.example.json","docs\GNEXUS_OPERATIONS_CONSOLE.md")){
  $p = Join-Path $TargetRepo $f
  if(Test-Path $p){ if((Get-Content $p -Raw) -match "C:\\CymaticsDev"){ $oldPathHits += $f } }
}
Add-Check "No legacy non-user CymaticsDev root literal in canonical files" ($oldPathHits.Count -eq 0) ($oldPathHits -join ",")
$failed = @($checks | Where-Object { -not $_.pass })
$status = if($failed.Count -eq 0){ "JUNIPERUS_GNEXUS_GOVERNED_POWER_BINDER_READY_LOCAL_CLOSEOUT" } else { "JUNIPERUS010_VERIFY_FAILED" }
$report = [ordered]@{ status=$status; generatedAt=(Get-Date).ToUniversalTime().ToString("o"); targetRepo=$TargetRepo; workspaceRoot=$WorkspaceRoot; checks=$checks; failedCount=$failed.Count }
$outDir = Join-Path $TargetRepo "data\gnexus\verification"
if(-not (Test-Path $outDir)){ New-Item -ItemType Directory -Force -Path $outDir | Out-Null }
$report | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 (Join-Path $outDir "juniperus010-verifier.json")
Write-Host "STATUS: $status"
foreach($c in $checks){ if($c.pass){ Write-Host "[OK] $($c.name)" } else { Write-Host "[FAIL] $($c.name) :: $($c.detail)" -ForegroundColor Red } }
if($failed.Count -gt 0){ exit 1 }
exit 0
