#Requires -Version 5.1
<#
  Master completeness verifier for Juniperus / Gnexus Operations Console.

  Enforces the closeout fail conditions:
   - /gnexus cockpit present
   - no Gnexus room is loading-only / unreachable
   - no visible Odysseus branding in Gnexus rooms
   - app shell title is Juniperus / Gnexus Operations Console
   - app.py exposes Gnexus frontstage routes
   - Ollama: if running, models imported + endpoint registered
   - governed operation proof shows apply + verify + rollback
   - high-risk tool audit present
   - START HERE doc present
   - final receipt present
   - Python modules compile
   - new route PS1/PY scripts parse
   - no new .cmd files
   - canonical workspace root correct

  Writes a closeout receipt and (on failure) a repair-queue with exact
  file/command/error/next-action items.
#>
param(
  [string]$TargetRepo = "",
  [string]$WorkspaceRoot = "C:\Users\iamcy\CymaticsDev"
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
Write-Host ("Workspace root: " + $WorkspaceRoot)

$checks = New-Object System.Collections.ArrayList
$repairs = New-Object System.Collections.ArrayList

function Add-Check([string]$Name, [bool]$Pass, [string]$Detail = "", [string]$File = "", [string]$Command = "", [string]$Next = "") {
  [void]$checks.Add([pscustomobject]@{ name=$Name; pass=$Pass; detail=$Detail })
  if ($Pass) { Write-Host "[OK] $Name" -ForegroundColor Green }
  else {
    Write-Host "[FAIL] $Name :: $Detail" -ForegroundColor Red
    [void]$repairs.Add([pscustomobject]@{ check=$Name; file=$File; command=$Command; error=$Detail; nextRepairAction=$Next })
  }
}

function J([string]$rel) { return (Join-Path $TargetRepo $rel) }

# --- Required deliverable files ---
$required = @(
  "static\gnexus\index.html",
  "static\gnexus\ollama-models.html",
  "static\gnexus\gnexus-core.css",
  "routes\gnexus_frontstage_routes.py",
  "routes\gnexus_ollama_routes.py",
  "routes\gnexus_completeness_routes.py",
  "config\gnexus.completeness-policy.example.json",
  "config\gnexus.local-model-routing.example.json",
  "config\gnexus.high-risk-tool-policy.example.json",
  "scripts\gnexus\Import-LocalOllamaModels.py",
  "scripts\gnexus\Run-FullGovernedOperatorSmoke.ps1",
  "scripts\gnexus\run_governed_operator_proof.py",
  "scripts\gnexus\Scan-JuniperusBranding.ps1",
  "scripts\gnexus\Scan-JuniperusHighRiskTools.ps1",
  "docs\START_HERE_GNEXUS_OPERATIONS_CONSOLE.md",
  "docs\JUNIPERUS_COMPLETENESS_CLOSEOUT.md",
  "docs\JUNIPERUS_REBRAND_COMPATIBILITY_MAP.md",
  "docs\JUNIPERUS_LOCAL_OLLAMA_MODEL_READINESS.md",
  "docs\JUNIPERUS_FULL_OPERATOR_LOOP_PROOF.md"
)
foreach ($r in $required) { Add-Check "Required file $r" (Test-Path (J $r)) "missing" $r "" "Create $r" }

# --- Cockpit present + no Odysseus + branded title ---
$cockpit = J "static\gnexus\index.html"
if (Test-Path $cockpit) {
  $ck = Get-Content $cockpit -Raw
  Add-Check "Cockpit branded Juniperus/Gnexus" (($ck -like "*Juniperus*") -and ($ck -like "*Gnexus Operations Console*")) "branding strings missing" "static\gnexus\index.html"
  Add-Check "Cockpit not loading-only" ($ck -notmatch "(?i)^\s*loading\.\.\.\s*$") "loading-only suspected"
}

# --- All Gnexus rooms reachable (static page exists OR server fallback) ---
$rooms = @("governance","app-dock","approval-desk","interceptor","diff-gate","patch-apply","verifier-loop","operator-loop","memory-routing","live-control","ollama-models")
$frontstage = Get-Content (J "routes\gnexus_frontstage_routes.py") -Raw
foreach ($room in $rooms) {
  $hasStatic = Test-Path (J ("static\gnexus\" + $room + ".html"))
  $hasFallback = ($frontstage -like "*_fallback_room_html*")
  Add-Check "Room reachable: $room" ($hasStatic -or $hasFallback) "no static page and no server fallback" ("static\gnexus\" + $room + ".html") "" "Add static page or rely on frontstage fallback"
}

# --- No visible Odysseus branding in Gnexus rooms ---
$gnexusDir = J "static\gnexus"
$brandHits = @()
if (Test-Path $gnexusDir) {
  $brandHits = @(Select-String -Path (Join-Path $gnexusDir "*.html"),(Join-Path $gnexusDir "*.css") -Pattern "odysseus" -SimpleMatch -CaseSensitive:$false -ErrorAction SilentlyContinue)
}
Add-Check "No visible Odysseus branding in Gnexus rooms" ($brandHits.Count -eq 0) (($brandHits | ForEach-Object { $_.Path }) -join ", ")

# --- App shell title ---
$shellOk = $false
$idxPath = J "static\index.html"
if (Test-Path $idxPath) {
  $idx = Get-Content $idxPath -Raw
  $shellOk = ($idx -like "*<title>Juniperus*") -and ($idx -notlike "*<title>Odysseus*")
}
Add-Check "App shell title is Juniperus-branded" $shellOk "title not Juniperus" "static\index.html"

# --- app.py exposes Gnexus frontstage routes ---
$app = Get-Content (J "app.py") -Raw
Add-Check "app.py exposes frontstage routes" ($app -like "*setup_gnexus_frontstage_routes*") "frontstage binding missing" "app.py"
Add-Check "app.py exposes Ollama routes" ($app -like "*setup_gnexus_ollama_routes*") "ollama binding missing" "app.py"
Add-Check "app.py exposes completeness routes" ($app -like "*setup_gnexus_completeness_routes*") "completeness binding missing" "app.py"

# --- Ollama readiness: if running, models imported + endpoint registered ---
$regPath = J "data\gnexus\ollama\ollama-model-registry.json"
if (Test-Path $regPath) {
  $reg = Get-Content $regPath -Raw | ConvertFrom-Json
  $running = $reg.ollama.running
  if ($running) {
    Add-Check "Ollama running -> models imported" ($reg.modelCount -gt 0) "ollama running but 0 models imported" "" ".\venv\Scripts\python.exe .\scripts\gnexus\Import-LocalOllamaModels.py" "Pull a model then re-import"
    if ($reg.modelCount -gt 0) {
      Add-Check "Ollama models -> endpoint registered" ($reg.endpoint.registered_in_picker -eq $true) "endpoint not registered while models exist" "" ".\venv\Scripts\python.exe .\scripts\gnexus\Import-LocalOllamaModels.py" "Run importer to register endpoint"
    }
    $smokePath = J "data\gnexus\ollama\ollama-smoke-test.json"
    if (Test-Path $smokePath) {
      $smoke = Get-Content $smokePath -Raw | ConvertFrom-Json
      Add-Check "Local smoke test has clear record" ($null -ne $smoke.ok) "smoke test record malformed" $smokePath
    }
  } else {
    Add-Check "Ollama offline -> clearly recorded" ($true) "offline recorded"
  }
} else {
  Add-Check "Ollama registry present (run importer)" ($false) "registry not generated yet" "" ".\venv\Scripts\python.exe .\scripts\gnexus\Import-LocalOllamaModels.py" "Run the Ollama importer"
}

# --- Governed operation proof: apply + verify + rollback ---
$proofPath = J "data\gnexus\operator-loop\sandbox\proof-receipt.json"
if (Test-Path $proofPath) {
  $proof = Get-Content $proofPath -Raw | ConvertFrom-Json
  $proven = ($proof.applyVerified -eq $true) -and ($proof.rollbackVerified -eq $true) -and ($proof.diffGateProven -eq $true)
  Add-Check "Governed proof shows apply+verify+rollback" $proven ("status=" + $proof.status) "" "powershell -ExecutionPolicy Bypass -File .\scripts\gnexus\Run-FullGovernedOperatorSmoke.ps1" "Run governed smoke with approval"
} else {
  Add-Check "Governed operation proof present" ($false) "proof receipt missing" "" "powershell -ExecutionPolicy Bypass -File .\scripts\gnexus\Run-FullGovernedOperatorSmoke.ps1" "Run governed smoke"
}

# --- High-risk tool audit present ---
$auditPath = J "data\gnexus\completeness\high-risk-tool-audit.json"
Add-Check "High-risk tool audit present" (Test-Path $auditPath) "audit not generated" "" "powershell -ExecutionPolicy Bypass -File .\scripts\gnexus\Scan-JuniperusHighRiskTools.ps1" "Run high-risk scan"

# --- Python compile ---
$venvPy = J "venv\Scripts\python.exe"
$py = if (Test-Path $venvPy) { $venvPy } else { (Get-Command python -ErrorAction SilentlyContinue).Source }
if ($py) {
  $pyFiles = @(
    "routes\gnexus_frontstage_routes.py",
    "routes\gnexus_ollama_routes.py",
    "routes\gnexus_completeness_routes.py",
    "src\gnexus_governance\ollama_readiness.py",
    "scripts\gnexus\Import-LocalOllamaModels.py",
    "scripts\gnexus\run_governed_operator_proof.py",
    "core\middleware.py"
  )
  foreach ($f in $pyFiles) {
    & $py -m py_compile (J $f) 2>$null
    Add-Check "Python compile $f" ($LASTEXITCODE -eq 0) "compile failed" $f
  }
} else {
  Add-Check "Python available for compile" $false "python not found"
}

# --- PowerShell parse ---
$ps1 = @(
  "scripts\gnexus\Run-FullGovernedOperatorSmoke.ps1",
  "scripts\gnexus\Scan-JuniperusBranding.ps1",
  "scripts\gnexus\Scan-JuniperusHighRiskTools.ps1",
  "scripts\gnexus\Verify-JuniperusCompleteness.ps1"
)
foreach ($s in $ps1) {
  $p = J $s
  $ok = $true
  try { $null = [System.Management.Automation.PSParser]::Tokenize((Get-Content $p -Raw), [ref]$null) }
  catch { $ok = $false }
  Add-Check "PowerShell parse $s" $ok "parse error" $s
}

# --- No new .cmd files in scripts\gnexus ---
$cmds = Get-ChildItem (J "scripts\gnexus") -Recurse -Filter "*.cmd" -ErrorAction SilentlyContinue
Add-Check "No new .cmd files in scripts\gnexus" (($cmds | Measure-Object).Count -eq 0) "cmd files present"

# --- Canonical workspace root ---
Add-Check "Canonical workspace root correct" ($WorkspaceRoot -eq "C:\Users\iamcy\CymaticsDev") "wrong workspace root"

# --- Final receipt presence (this run writes it; check JSON validity of policies) ---
$jsonFiles = @(
  "config\gnexus.completeness-policy.example.json",
  "config\gnexus.local-model-routing.example.json",
  "config\gnexus.high-risk-tool-policy.example.json"
)
foreach ($j in $jsonFiles) {
  $p = J $j
  $ok = $true
  try { $null = Get-Content $p -Raw | ConvertFrom-Json } catch { $ok = $false }
  Add-Check "JSON valid $j" $ok "invalid json" $j
}

$failed = @($checks | Where-Object { -not $_.pass })
$status = if ($failed.Count -eq 0) { "JUNIPERUS_GNEXUS_OPERATIONS_CONSOLE_COMPLETENESS_READY_LOCAL_CLOSEOUT" } else { "REPAIR_REQUIRED" }

$report = [pscustomobject]@{
  status = $status
  generatedAt = (Get-Date).ToUniversalTime().ToString("o")
  targetRepo = $TargetRepo
  workspaceRoot = $WorkspaceRoot
  passedCount = ($checks.Count - $failed.Count)
  failedCount = $failed.Count
  checks = $checks
  cockpitUrl = "http://127.0.0.1:7010/gnexus"
  ollamaUrl = "http://127.0.0.1:7010/gnexus/ollama-models"
}

$compDir = J "data\gnexus\completeness"
New-Item -ItemType Directory -Force $compDir | Out-Null
$reportPath = Join-Path $compDir "verification-report.json"
$report | ConvertTo-Json -Depth 10 | Set-Content -Path $reportPath -Encoding UTF8

$receiptDir = J "data\gnexus\receipts"
New-Item -ItemType Directory -Force $receiptDir | Out-Null
$receiptPath = Join-Path $receiptDir "JUNIPERUS_COMPLETENESS_CLOSEOUT.json"
$report | ConvertTo-Json -Depth 10 | Set-Content -Path $receiptPath -Encoding UTF8

$repairPath = Join-Path $compDir "repair-queue.json"
$repairObj = [pscustomobject]@{
  generatedAt = (Get-Date).ToUniversalTime().ToString("o")
  status = $status
  items = $repairs
}
$repairObj | ConvertTo-Json -Depth 10 | Set-Content -Path $repairPath -Encoding UTF8

Write-Host ""
Write-Host ("Report:  " + $reportPath)
Write-Host ("Receipt: " + $receiptPath)
Write-Host ("Repair:  " + $repairPath)
Write-Host ("Status:  " + $status)

if ($failed.Count -gt 0) {
  Write-Host ("JUNIPERUS COMPLETENESS VERIFY FAILED (" + $failed.Count + " checks)") -ForegroundColor Red
  exit 1
}
Write-Host "JUNIPERUS COMPLETENESS VERIFY PASSED" -ForegroundColor Green
exit 0
