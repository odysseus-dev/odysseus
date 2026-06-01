#Requires -Version 5.1
param(
  [string]$WorkspaceRoot = "C:\Users\iamcy\CymaticsDev",
  [string]$TargetRepo = ""
)
$ErrorActionPreference = "Stop"
function New-Dir($p){ if(-not (Test-Path $p)){ New-Item -ItemType Directory -Force -Path $p | Out-Null } }
function HasFile($dir,$name){ Test-Path (Join-Path $dir $name) }
function Classify($dir){
  if(HasFile $dir "package.json") { return "node" }
  if(HasFile $dir "vite.config.js" -or HasFile $dir "vite.config.ts") { return "vite" }
  if(HasFile $dir "next.config.js" -or HasFile $dir "next.config.mjs") { return "next" }
  if(HasFile $dir "pyproject.toml" -or HasFile $dir "requirements.txt") { return "python" }
  if(HasFile $dir "START.bat" -or HasFile $dir "RUN_ME_FIRST.bat") { return "drop_pack_or_local_tool" }
  return "folder"
}
if(-not $TargetRepo){ $TargetRepo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }
$dataRoot = Join-Path $TargetRepo "data\gnexus"
New-Dir $dataRoot
$projects = New-Object System.Collections.ArrayList
$apps = New-Object System.Collections.ArrayList
$roots = @($WorkspaceRoot, (Join-Path $WorkspaceRoot "00_SYSTEMS"), (Join-Path $WorkspaceRoot "01_ACTIVE_REPOS"), (Join-Path $WorkspaceRoot "06_INFINITE_BRAIN")) | Select-Object -Unique
foreach($root in $roots){
  if(Test-Path $root){
    $dirs = @(Get-ChildItem -Path $root -Directory -ErrorAction SilentlyContinue)
    foreach($d in $dirs){
      $kind = Classify $d.FullName
      $record = [ordered]@{ id = ($d.Name -replace '[^A-Za-z0-9_-]','-').ToLower(); name=$d.Name; path=$d.FullName; kind=$kind; detectedAt=(Get-Date).ToUniversalTime().ToString("o") }
      [void]$projects.Add($record)
      if($kind -ne "folder"){
        $launch = ""
        $verify = ""
        if(HasFile $d.FullName "START.bat"){ $launch = "START.bat" }
        elseif(HasFile $d.FullName "RUN_ME_FIRST.bat"){ $launch = "RUN_ME_FIRST.bat" }
        elseif($kind -eq "node"){ $launch = "npm run dev"; $verify = "npm run build" }
        elseif($kind -eq "python"){ $launch = "python app.py" }
        [void]$apps.Add([ordered]@{ id=$record.id; name=$d.Name; root=$d.FullName; kind=$kind; launch=$launch; verify=$verify; status="DISCOVERED_NOT_LAUNCHED"; approvalRequired=$true })
      }
    }
  }
}
$projPayload = [ordered]@{ schemaVersion="JUNIPERUS_PROJECT_REGISTRY_v0_1_2"; workspaceRoot=$WorkspaceRoot; updatedAt=(Get-Date).ToUniversalTime().ToString("o"); items=$projects }
$appPayload = [ordered]@{ schemaVersion="JUNIPERUS_APP_REGISTRY_v0_1_2"; workspaceRoot=$WorkspaceRoot; updatedAt=(Get-Date).ToUniversalTime().ToString("o"); items=$apps }
$projPayload | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 (Join-Path $dataRoot "project-registry.json")
$appPayload | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 (Join-Path $dataRoot "app-registry.json")
Write-Host "Scanned workspace: $WorkspaceRoot"
Write-Host "Projects: $($projects.Count)"
Write-Host "Apps/tools: $($apps.Count)"
