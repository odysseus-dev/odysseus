<#
Install the reviewed SearXNG launcher fixes (commit a2320ad in F:\Sonnet-Worktrees\searxng-launcher-fix).
Run it yourself, in a normal PowerShell, outside Hermes. It:
  - copies ONLY the seven reviewed files (no wildcards); settings.yml, config, .venv, logs, api_pool are never touched;
  - backs up each current file first, then verifies every copy by SHA-256 against the reviewed source;
  - does not stop, start or restart SearXNG. The running process keeps its already-loaded scripts; the fixes apply to the next start/stop.
Dry run by default; add -Apply to copy.
#>
param(
    [string]$Source = 'F:\Sonnet-Worktrees\searxng-launcher-fix',
    [string]$Target = (Join-Path $env:USERPROFILE 'Apps\searxng-windows'),
    [string]$BackupRoot = 'F:\Odysseus-Backups',
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$Files = @(
    'scripts\check.ps1', 'scripts\run.ps1', 'scripts\start.ps1', 'scripts\stop.ps1',
    'check-searxng.cmd', 'start-searxng.cmd', 'stop-searxng.cmd'
)
$Expected = @{
    'scripts\check.ps1'  = 'FDA69F527449DFF4EC4A3BF7EE1008A2720007A040DBE6B1E9AD0D4249AC24F1'
    'scripts\run.ps1'    = '702D10CE1C55BA444288D999E4C458D9A4C016DA01B5E48D3FEC5281277D58D1'
    'scripts\start.ps1'  = 'D4BD0B44F72F8302A09516AC2A72DFD695FA20A26E13BF59B437C153F814DEE4'
    'scripts\stop.ps1'   = '39DB5A657AF336A6E8397F06AFE310A7B74D29F90F02F1E9383E0B092C6EE5B5'
    'check-searxng.cmd'  = '64CF592F4B9B2BD2DE5E95CDB3AFA2AAA56BB14A22B6E2201C1351A525D7D9DF'
    'start-searxng.cmd'  = 'AB9EEB912BDCCD37DE867D91186EF4524526DA824FCDBBCE259117A7AAE79BB7'
    'stop-searxng.cmd'   = '01FAB160B71345F4DAA8D7D498ABD33A5F207E4846A70E691059E7F6A5215C60'
}
foreach ($f in $Files) {
    $s = Join-Path $Source $f
    if (-not (Test-Path -LiteralPath $s)) { throw "Missing source file: $s" }
    $h = (Get-FileHash -LiteralPath $s -Algorithm SHA256).Hash
    if ($h -ne $Expected[$f]) { throw "Source $f does not match the reviewed hash ($h)" }
    if (-not (Test-Path -LiteralPath (Join-Path $Target $f))) { throw "Target file not found (wrong folder?): $(Join-Path $Target $f)" }
}
Write-Host "Reviewed sources verified. Target: $Target"
foreach ($f in $Files) {
    $same = (Get-FileHash -LiteralPath (Join-Path $Target $f) -Algorithm SHA256).Hash -eq $Expected[$f]
    Write-Host ("  {0,-22} {1}" -f $f, $(if ($same) { 'already installed' } else { 'will be replaced' }))
}
if (-not $Apply) { Write-Host 'Dry run. Nothing changed. Re-run with -Apply.'; return }

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $BackupRoot "searxng-launchers-before-fix-$stamp"
foreach ($f in $Files) {
    $dest = Join-Path $backup $f
    New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
    Copy-Item -LiteralPath (Join-Path $Target $f) -Destination $dest
}
foreach ($f in $Files) { Copy-Item -LiteralPath (Join-Path $Source $f) -Destination (Join-Path $Target $f) -Force }
$bad = @()
foreach ($f in $Files) {
    if ((Get-FileHash -LiteralPath (Join-Path $Target $f) -Algorithm SHA256).Hash -ne $Expected[$f]) { $bad += $f }
}
if ($bad) { Write-Error "Hash mismatch after copy: $($bad -join ', '). Restore from $backup"; exit 1 }
Write-Host "Installed and verified (7 files). Backup: $backup"
Write-Host 'SearXNG was not restarted. The fixes apply the next time you start or stop it.'
