#Requires -Version 5.1
param(
  [string]$TargetRepo = "",
  [string]$WorkspaceRoot = "C:\Users\iamcy\CymaticsDev"
)
$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($TargetRepo)) {
  $TargetRepo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
$checks = New-Object System.Collections.ArrayList
function Add-Check($Name, $Pass, $Detail) {
  [void]$checks.Add([ordered]@{ name=$Name; pass=[bool]$Pass; detail=[string]$Detail })
}
Add-Check "Target repo exists" (Test-Path $TargetRepo) $TargetRepo
Add-Check "Workspace root exists" (Test-Path $WorkspaceRoot) $WorkspaceRoot
Add-Check "App dock route file present" (Test-Path (Join-Path $TargetRepo "routes\gnexus_app_dock_routes.py")) ""
Add-Check "App dock static page present" (Test-Path (Join-Path $TargetRepo "static\gnexus\app-dock.html")) ""
Add-Check "App registry present" (Test-Path (Join-Path $TargetRepo "data\gnexus\app-registry.json")) ""
Add-Check "Launch queue present" (Test-Path (Join-Path $TargetRepo "data\gnexus\app-dock\launch-queue.json")) ""
$appPy = Join-Path $TargetRepo "app.py"
$bound = $false
if (Test-Path $appPy) {
  $txt = Get-Content $appPy -Raw -Encoding UTF8
  $bound = $txt.Contains("setup_gnexus_app_dock_routes")
}
Add-Check "app.py app dock route bound" $bound "setup_gnexus_app_dock_routes"
$failed = @($checks | Where-Object { -not $_.pass }).Count
$status = if ($failed -eq 0) { "JUNIPERUS_APP_DOCK_RUNTIME_LAUNCHER_READY_LOCAL_CLOSEOUT" } else { "JUNIPERUS_APP_DOCK_RUNTIME_LAUNCHER_VERIFY_FAILED" }
$out = [ordered]@{
  status=$status
  generatedAt=(Get-Date).ToUniversalTime().ToString("o")
  targetRepo=$TargetRepo
  workspaceRoot=$WorkspaceRoot
  checks=@($checks)
  failedCount=$failed
  appDockUrl="http://127.0.0.1:7010/gnexus/app-dock"
  restartRequired=$true
}
$out | ConvertTo-Json -Depth 20
if ($failed -ne 0) { exit 1 }
exit 0
