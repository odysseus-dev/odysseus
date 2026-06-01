#Requires -Version 5.1
param(
  [string]$WorkspaceRoot = "C:\Users\iamcy\CymaticsDev",
  [string]$TargetRepo = "",
  [int]$MaxDepth = 5
)
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($TargetRepo)) {
  $TargetRepo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
if (-not (Test-Path $WorkspaceRoot)) { throw "WorkspaceRoot not found: $WorkspaceRoot" }
if (-not (Test-Path $TargetRepo)) { throw "TargetRepo not found: $TargetRepo" }

$skip = @(".git","node_modules","venv",".venv","__pycache__",".next","dist","build","logs","data",".mypy_cache",".pytest_cache",".cache")
$apps = New-Object System.Collections.ArrayList

function Get-RelPath([string]$Root, [string]$Path) {
  $uRoot = New-Object System.Uri(($Root.TrimEnd('\') + '\'))
  $uPath = New-Object System.Uri($Path)
  return [System.Uri]::UnescapeDataString($uRoot.MakeRelativeUri($uPath).ToString()).Replace('/','\')
}

function New-SafeId([string]$Text) {
  $id = ($Text.ToLowerInvariant() -replace '[^a-z0-9]+','-').Trim('-')
  if ([string]::IsNullOrWhiteSpace($id)) { $id = "app" }
  if ($id.Length -gt 80) { $id = $id.Substring(0,80) }
  return $id
}

function Scan-Dir([string]$Dir, [int]$Depth) {
  $signals = New-Object System.Collections.ArrayList
  $exact = @("package.json","pyproject.toml","requirements.txt","app.py","main.py","START.bat","VERIFY.bat","docker-compose.yml","docker-compose.yaml")
  foreach ($e in $exact) {
    if (Test-Path (Join-Path $Dir $e)) { [void]$signals.Add($e) }
  }
  Get-ChildItem -LiteralPath $Dir -File -ErrorAction SilentlyContinue | ForEach-Object {
    $n = $_.Name.ToLowerInvariant()
    if ($n.StartsWith("vite.config") -or $n.StartsWith("next.config")) { [void]$signals.Add($_.Name) }
  }

  if ($signals.Count -gt 0) {
    $commands = [ordered]@{}
    $urls = New-Object System.Collections.ArrayList
    $type = "generic-tool"
    $confidence = "low"

    if (Test-Path (Join-Path $Dir "START.bat")) {
      $commands.start = "START.bat"
      $type = "gnexus-droppack-or-local-tool"
      $confidence = "medium"
    }
    if (Test-Path (Join-Path $Dir "VERIFY.bat")) {
      $commands.verify = "VERIFY.bat"
    }

    if (Test-Path (Join-Path $Dir "package.json")) {
      try {
        $pkg = Get-Content (Join-Path $Dir "package.json") -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($pkg.scripts.dev) { $commands.start = "npm run dev" }
        elseif ($pkg.scripts.start) { $commands.start = "npm start" }
        if ($pkg.scripts.build) { $commands.verify = "npm run build" }
      } catch {}
      $hasVite = @(Get-ChildItem -LiteralPath $Dir -File -ErrorAction SilentlyContinue | Where-Object { $_.Name.ToLowerInvariant().StartsWith("vite.config") }).Count -gt 0
      $hasNext = @(Get-ChildItem -LiteralPath $Dir -File -ErrorAction SilentlyContinue | Where-Object { $_.Name.ToLowerInvariant().StartsWith("next.config") }).Count -gt 0
      if ($hasVite) { $type = "vite-app"; [void]$urls.Add("http://127.0.0.1:5173") }
      elseif ($hasNext) { $type = "next-app"; [void]$urls.Add("http://127.0.0.1:3000") }
      else { $type = "node-app" }
      $confidence = "high"
    }

    if ((Test-Path (Join-Path $Dir "pyproject.toml")) -or (Test-Path (Join-Path $Dir "requirements.txt"))) {
      if (Test-Path (Join-Path $Dir "app.py")) {
        if (-not $commands.Contains("start")) { $commands.start = "python -m uvicorn app:app --host 127.0.0.1 --port 8000" }
        [void]$urls.Add("http://127.0.0.1:8000")
        $type = "python-fastapi-or-asgi"
        $confidence = "medium"
      } elseif (Test-Path (Join-Path $Dir "main.py")) {
        if (-not $commands.Contains("start")) { $commands.start = "python main.py" }
        $type = "python-app"
        $confidence = "medium"
      } else {
        $type = "python-project"
        $confidence = "medium"
      }
    }

    if ((Test-Path (Join-Path $Dir "docker-compose.yml")) -or (Test-Path (Join-Path $Dir "docker-compose.yaml"))) {
      if (-not $commands.Contains("containerStartCandidate")) { $commands.containerStartCandidate = "docker compose up" }
      if ($type -eq "generic-tool") { $type = "docker-compose-app" } else { $type = $type + "+docker" }
      if ($confidence -eq "low") { $confidence = "medium" }
    }

    $rel = Get-RelPath $WorkspaceRoot $Dir
    $app = [ordered]@{
      id = New-SafeId $rel
      name = Split-Path $Dir -Leaf
      root = $Dir
      relativePath = $rel
      type = $type
      confidence = $confidence
      signals = @($signals | Sort-Object -Unique)
      commands = $commands
      urls = @($urls | Sort-Object -Unique)
      launchApprovalRequired = $true
      runtimeStartEnabled = $false
    }
    [void]$apps.Add($app)
  }

  if ($Depth -ge $MaxDepth) { return }
  Get-ChildItem -LiteralPath $Dir -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    if ($skip -contains $_.Name) { return }
    if ($_.Name.StartsWith(".") -and $_.Name -notin @(".gnexus",".gnx")) { return }
    Scan-Dir $_.FullName ($Depth + 1)
  }
}

Scan-Dir $WorkspaceRoot 0

$dataDir = Join-Path $TargetRepo "data\gnexus"
$appDockDir = Join-Path $dataDir "app-dock"
$mcDir = Join-Path $dataDir "mission-control"
New-Item -ItemType Directory -Force $dataDir, $appDockDir, $mcDir | Out-Null

$registry = [ordered]@{
  schema = "gnexus.app-registry.v1"
  generatedAt = (Get-Date).ToUniversalTime().ToString("o")
  workspaceRoot = $WorkspaceRoot
  status = "SCAN_COMPLETE"
  appCount = $apps.Count
  apps = @($apps)
}
$registry | ConvertTo-Json -Depth 20 | Set-Content -Encoding UTF8 (Join-Path $dataDir "app-registry.json")

$state = [ordered]@{
  schema = "gnexus.app-dock-state.v1"
  status = "JUNIPERUS_APP_DOCK_RUNTIME_LAUNCHER_READY_LOCAL"
  generatedAt = (Get-Date).ToUniversalTime().ToString("o")
  workspaceRoot = $WorkspaceRoot
  appCount = $apps.Count
  runtimeStartEnabled = $false
  launchApprovalRequired = $true
  appDockUrl = "http://127.0.0.1:7010/gnexus/app-dock"
}
$state | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 (Join-Path $mcDir "app-dock-state.json")

$launchQueue = Join-Path $appDockDir "launch-queue.json"
if (-not (Test-Path $launchQueue)) {
  @{ schema="gnexus.launch-queue.v1"; items=@() } | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 $launchQueue
}
$sessions = Join-Path $appDockDir "runtime-sessions.json"
if (-not (Test-Path $sessions)) {
  @{ schema="gnexus.runtime-sessions.v1"; items=@() } | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 $sessions
}

Write-Host ("App scan complete. Detected {0} app/tool candidates." -f $apps.Count)
Write-Host ("Registry: " + (Join-Path $dataDir "app-registry.json"))
