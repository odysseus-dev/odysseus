#Requires -Version 5.1
param(
  [string]$TargetRepo = '',
  [string]$WorkspaceRoot = 'C:\Users\iamcy\CymaticsDev'
)

$ErrorActionPreference = 'Stop'
$checks = New-Object System.Collections.ArrayList

function Add-Check {
  param(
    [string]$Name,
    [bool]$Pass,
    [string]$Detail = ''
  )
  $item = [ordered]@{
    name = $Name
    pass = $Pass
    detail = $Detail
  }
  [void]$script:checks.Add($item)
  if ($Pass) {
    Write-Host ('[OK] ' + $Name) -ForegroundColor Green
  } else {
    Write-Host ('[FAIL] ' + $Name + ' :: ' + $Detail) -ForegroundColor Red
  }
}

function Resolve-TargetRepo {
  param([string]$Requested)

  if ($Requested -and (Test-Path (Join-Path $Requested 'app.py'))) {
    return (Resolve-Path $Requested).Path
  }

  $candidates = @(
    'C:\Users\iamcy\CymaticsDev\00_SYSTEMS\Juniperus',
    'C:\Users\iamcy\CymaticsDev\01_ACTIVE_REPOS\Juniperus',
    'C:\Users\iamcy\Juniperus',
    (Join-Path (Get-Location).Path 'Juniperus'),
    (Get-Location).Path
  )

  foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path (Join-Path $candidate 'app.py'))) {
      return (Resolve-Path $candidate).Path
    }
  }

  return $null
}

function Test-JsonFile {
  param([string]$Path)
  try {
    Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json | Out-Null
    return $true
  } catch {
    return $false
  }
}

function Write-Utf8 {
  param([string]$Path, [string]$Content)
  $dir = Split-Path -Parent $Path
  if ($dir -and -not (Test-Path $dir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
  }
  Set-Content -LiteralPath $Path -Value $Content -Encoding UTF8
}

$packageRoot = Split-Path -Parent $PSScriptRoot
$target = Resolve-TargetRepo -Requested $TargetRepo

Write-Host '============================================================'
Write-Host 'JUNIPERUS030 v0.1.1 VERIFYFIX - VERIFY'
Write-Host '============================================================'
Write-Host ('Package root : ' + $packageRoot)
Write-Host ('Target repo  : ' + ($(if ($target) { $target } else { '<not resolved>' })))
Write-Host ('Workspace    : ' + $WorkspaceRoot)
Write-Host '============================================================'

$acceptedRoot = $false
if ($packageRoot -eq 'C:\GNX\JUNIPERUS030') { $acceptedRoot = $true }
if ($packageRoot -eq 'C:\GNX\JUNIPERUS030\JUNIPERUS030') { $acceptedRoot = $true }
Add-Check -Name 'Package at accepted short root' -Pass $acceptedRoot -Detail ('current=' + $packageRoot)

Add-Check -Name 'Target repo resolved' -Pass ([bool]$target) -Detail $target
if (-not $target) {
  $reportRoot = Join-Path $packageRoot 'reports'
  New-Item -ItemType Directory -Force -Path $reportRoot | Out-Null
  $report = [ordered]@{
    status = 'JUNIPERUS030_VERIFY_FAILED'
    generatedAt = (Get-Date).ToUniversalTime().ToString('o')
    packageRoot = $packageRoot
    targetRepo = $null
    workspaceRoot = $WorkspaceRoot
    checks = $checks
    failedCount = 1
  }
  Write-Utf8 -Path (Join-Path $reportRoot 'JUNIPERUS030_VERIFY_REPORT.json') -Content (($report | ConvertTo-Json -Depth 8))
  exit 1
}

$requiredFiles = @(
  'docs\JUNIPERUS_APPROVAL_QUEUE_HUMAN_DECISION_DESK.md',
  'config\gnexus.approval-desk-policy.example.json',
  'src\gnexus_governance\approval_desk.py',
  'routes\gnexus_approval_desk_routes.py',
  'scripts\gnexus\Verify-JuniperusApprovalDesk.ps1',
  'static\gnexus\approval-desk.html',
  'data\gnexus\approval-desk\decision-ledger.json',
  'data\gnexus\approval-queue.json',
  'data\gnexus\mission-control\approval-desk-state.json'
)

foreach ($rel in $requiredFiles) {
  $full = Join-Path $target $rel
  Add-Check -Name ('Required file ' + $rel) -Pass (Test-Path $full) -Detail $full
}

$appPath = Join-Path $target 'app.py'
$appText = ''
if (Test-Path $appPath) {
  $appText = Get-Content -LiteralPath $appPath -Raw
}
Add-Check -Name 'app.py approval desk route bound' -Pass ($appText -match 'setup_gnexus_approval_desk_routes') -Detail 'setup_gnexus_approval_desk_routes'

$jsonFiles = @(
  'config\gnexus.approval-desk-policy.example.json',
  'data\gnexus\approval-desk\decision-ledger.json',
  'data\gnexus\approval-queue.json',
  'data\gnexus\mission-control\approval-desk-state.json'
)
foreach ($rel in $jsonFiles) {
  $full = Join-Path $target $rel
  $ok = (Test-Path $full) -and (Test-JsonFile -Path $full)
  Add-Check -Name ('JSON valid ' + $rel) -Pass $ok -Detail $full
}

$cmdFiles = @()
try {
  $cmdFiles = Get-ChildItem -LiteralPath (Join-Path $target 'static\gnexus') -Filter '*.cmd' -Recurse -ErrorAction SilentlyContinue
} catch {
  $cmdFiles = @()
}
Add-Check -Name 'No new Gnexus .cmd files' -Pass (($cmdFiles | Measure-Object).Count -eq 0) -Detail (($cmdFiles | ForEach-Object { $_.FullName }) -join '; ')

$legacyBad = $false
$scanFiles = @(
  'docs\JUNIPERUS_APPROVAL_QUEUE_HUMAN_DECISION_DESK.md',
  'config\gnexus.approval-desk-policy.example.json',
  'static\gnexus\approval-desk.html'
)
foreach ($rel in $scanFiles) {
  $full = Join-Path $target $rel
  if (Test-Path $full) {
    $txt = Get-Content -LiteralPath $full -Raw
    if ($txt -match 'C:\\CymaticsDev') {
      $legacyBad = $true
    }
  }
}
Add-Check -Name 'No legacy non-user CymaticsDev root literal in approval desk files' -Pass (-not $legacyBad) -Detail ''

$failed = @($checks | Where-Object { -not $_.pass })
$status = if ($failed.Count -eq 0) { 'JUNIPERUS_APPROVAL_QUEUE_HUMAN_DECISION_DESK_READY_LOCAL_CLOSEOUT' } else { 'JUNIPERUS030_VERIFY_FAILED' }

$report = [ordered]@{
  status = $status
  generatedAt = (Get-Date).ToUniversalTime().ToString('o')
  packageRoot = $packageRoot
  targetRepo = $target
  workspaceRoot = $WorkspaceRoot
  checks = $checks
  failedCount = $failed.Count
  restartRequired = $true
  approvalDeskUrl = 'http://127.0.0.1:7010/gnexus/approval-desk'
  boundary = [ordered]@{
    runtimeExecution = 'LOCKED'
    shellInterceptionActive = $false
    fileInterceptionActive = $false
    externalReadsWrites = $false
    connectorCalls = $false
    secretsStored = $false
  }
}

$reportRoot = Join-Path $packageRoot 'reports'
New-Item -ItemType Directory -Force -Path $reportRoot | Out-Null
$reportJson = $report | ConvertTo-Json -Depth 10
Write-Utf8 -Path (Join-Path $reportRoot 'JUNIPERUS030_VERIFY_REPORT.json') -Content $reportJson
Write-Utf8 -Path (Join-Path $target 'data\gnexus\mission-control\approval-desk-state.json') -Content $reportJson

Write-Host ''
if ($failed.Count -eq 0) {
  Write-Host 'JUNIPERUS030 VERIFY PASSED' -ForegroundColor Green
  Write-Host $status -ForegroundColor Green
  exit 0
}

Write-Host 'JUNIPERUS030 VERIFY FAILED' -ForegroundColor Red
Write-Host ('Failed checks: ' + $failed.Count) -ForegroundColor Red
exit 1
