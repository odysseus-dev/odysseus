param(
    [string]$BaseUrl = "http://localhost:8000",
    [string]$AdminToken = "",
    [string]$AdminCookie = "",
    [string]$ExpectedCommit = "0ac4cc0d",
    [string]$RepoPath = "C:\Users\Hachi\Projects\odysseus"
)

$BaseUrl = $BaseUrl.TrimEnd("/")
$failures = 0
function Pass($msg) { Write-Host "[PASS] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red; $script:failures++ }

Write-Host "`n=== OdyAI Pre-Launch Readiness & Smoke Verification ===" -ForegroundColor Cyan
Write-Host "Target Server : $BaseUrl"
Write-Host "Expected Hash : $ExpectedCommit`n"

if (Test-Path $RepoPath) {
    $branch = (git -C $RepoPath rev-parse --abbrev-ref HEAD).Trim()
    $head   = (git -C $RepoPath rev-parse --short HEAD).Trim()
    if ($branch -eq "dev" -and $head -eq $ExpectedCommit) { Pass "Local repo on '$branch' at $head" }
    else { Warn "Local repo on '$branch' ($head) - expected 'dev' ($ExpectedCommit)" }

    $appJs = Get-Content "$RepoPath\static\app.js" -Raw -Encoding utf8
    $swJs  = Get-Content "$RepoPath\static\sw.js" -Raw -Encoding utf8
    if ($appJs -match "admin\.js\?v=20261113opsreleasev12" -and $swJs -match "admin\.js\?v=20261113opsreleasev12") {
        Pass "admin.js cache-buster synced at v12 in app.js & sw.js"
    } else { Fail "admin.js cache-buster mismatch in app.js / sw.js" }

    if ($appJs -match "tasks\.js\?v=20261113opsreleasev12" -and $swJs -match "tasks\.js\?v=20261113opsreleasev12") {
        Pass "tasks.js cache-buster synced at v12 in app.js & sw.js"
    } else { Fail "tasks.js cache-buster mismatch in app.js / sw.js" }
}

try {
    $ver = Invoke-RestMethod -Uri "$BaseUrl/api/version" -Method Get -TimeoutSec 10
    $verJson = $ver | ConvertTo-Json -Compress
    if ($verJson -match $ExpectedCommit) { Pass "/api/version matches deployed commit ($ExpectedCommit)" }
    else { Warn "/api/version responded ($verJson), but did not include '$ExpectedCommit'" }
} catch { Fail "/api/version unreachable at $BaseUrl/api/version : $($_.Exception.Message)" }

foreach ($route in @("/api/diagnostics/ops-summary", "/api/diagnostics/effects", "/api/diagnostics/services", "/api/diagnostics/logs")) {
    $status = 0
    try {
        $resp = Invoke-WebRequest -Uri "$BaseUrl$route" -Method Get -TimeoutSec 10 -UseBasicParsing
        $status = [int]$resp.StatusCode
    } catch {
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
    }
    if ($status -in @(401, 403)) { Pass "Unauthenticated $route properly blocked (HTTP $status)" }
    elseif ($status -eq 0) { Fail "Unreachable $route (server not listening at $BaseUrl)" }
    else { Fail "SECURITY: Unauthenticated $route returned HTTP $status instead of 401/403" }
}

$headers = @{}
if ($AdminToken)  { $headers["Authorization"] = "Bearer $AdminToken" }
if ($AdminCookie) { $headers["Cookie"]        = $AdminCookie }
if ($headers.Count -gt 0) {
    try {
        $null = Invoke-RestMethod -Uri "$BaseUrl/api/diagnostics/ops-summary" -Headers $headers -Method Get -TimeoutSec 10
        Pass "Authenticated /api/diagnostics/ops-summary OK"
    } catch { Fail "Authenticated /api/diagnostics/ops-summary failed: $($_.Exception.Message)" }
    try {
        $eff = Invoke-RestMethod -Uri "$BaseUrl/api/diagnostics/effects?limit=5&unsettled_only=true" -Headers $headers -Method Get -TimeoutSec 10
        Pass "Authenticated /api/diagnostics/effects?unsettled_only=true OK (unsettled runs: $($eff.count))"
    } catch { Fail "Authenticated /api/diagnostics/effects failed: $($_.Exception.Message)" }
} else { Warn "No -AdminToken or -AdminCookie provided - skipped authenticated Ops smoke checks" }

Write-Host "`n=== Summary ===" -ForegroundColor Cyan
if ($failures -eq 0) { Write-Host "ALL CHECKS PASSED - OdyAI is ready for launch." -ForegroundColor Green }
else { Write-Host "$failures CHECK(S) FAILED - resolve before cutting over traffic." -ForegroundColor Red }
