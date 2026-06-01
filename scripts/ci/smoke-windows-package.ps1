param(
    [Parameter(Mandatory = $true)][string]$ExePath,
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 7000,
    [int]$StartupTimeoutSec = 90,
    [string]$InstallRoot = "",
    [string]$LocalAppDataRoot = ""
)

$ErrorActionPreference = "Stop"

$resolvedExe = Resolve-Path -LiteralPath $ExePath -ErrorAction Stop
if (-not (Test-Path -LiteralPath $resolvedExe -PathType Leaf)) {
    throw "Executable not found: $ExePath"
}

$smokeRootBase = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { Join-Path $PWD "tmp" }
$runId = "port-$Port-$PID"
if (-not $InstallRoot) {
    $InstallRoot = Join-Path $smokeRootBase "odysseus-windows-install-smoke-$runId"
}
if (-not $LocalAppDataRoot) {
    $LocalAppDataRoot = Join-Path $smokeRootBase "odysseus-windows-localappdata-smoke-$runId"
}

Remove-Item -LiteralPath $InstallRoot, $LocalAppDataRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $InstallRoot, $LocalAppDataRoot | Out-Null

$installedExe = Join-Path $InstallRoot "odysseus.exe"
Copy-Item -LiteralPath $resolvedExe -Destination $installedExe -Force

$stdoutPath = Join-Path $InstallRoot "odysseus-windows-smoke.stdout.log"
$stderrPath = Join-Path $InstallRoot "odysseus-windows-smoke.stderr.log"

$env:ODYSSEUS_HOST = $BindHost
$env:ODYSSEUS_PORT = [string]$Port
$env:AUTH_ENABLED = "false"
$env:LOCALHOST_BYPASS = "true"
$env:ODYSSEUS_INPROCESS_TASKS = "0"
$env:ODYSSEUS_INPROCESS_POLLERS = "0"
$env:LOCALAPPDATA = $LocalAppDataRoot

$proc = Start-Process -FilePath $installedExe `
    -WorkingDirectory $InstallRoot `
    -PassThru `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath

try {
    $deadline = (Get-Date).AddSeconds($StartupTimeoutSec)
    $baseUrl = "http://$BindHost`:$Port"
    $healthUrl = "$baseUrl/api/health"
    $started = $false

    while ((Get-Date) -lt $deadline) {
        if ($proc.HasExited) {
            $stdout = if (Test-Path $stdoutPath) { Get-Content $stdoutPath -Raw } else { "" }
            $stderr = if (Test-Path $stderrPath) { Get-Content $stderrPath -Raw } else { "" }
            throw "Windows package exited early with code $($proc.ExitCode)`nSTDOUT:`n$stdout`nSTDERR:`n$stderr"
        }

        try {
            $resp = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 5
            if ($resp.StatusCode -eq 200) {
                $started = $true
                break
            }
        } catch {
        }

        Start-Sleep -Seconds 2
    }

    function Invoke-SmokeRequest {
        param(
            [Parameter(Mandatory = $true)][string]$Path,
            [Parameter(Mandatory = $true)][int]$MinLength
        )

        $url = "$baseUrl$Path"
        $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10
        if ($resp.StatusCode -ne 200) {
            throw "Expected 200 from $Path, got $($resp.StatusCode)"
        }
        if (($resp.Content.Length) -lt $MinLength) {
            throw "Expected $Path response length >= $MinLength, got $($resp.Content.Length)"
        }
        Write-Host "Smoke endpoint passed: $Path ($($resp.Content.Length) bytes)"
    }

    if (-not $started) {
        $stdout = if (Test-Path $stdoutPath) { Get-Content $stdoutPath -Raw } else { "" }
        $stderr = if (Test-Path $stderrPath) { Get-Content $stderrPath -Raw } else { "" }
        throw "Windows package did not become healthy at $healthUrl within $StartupTimeoutSec seconds.`nSTDOUT:`n$stdout`nSTDERR:`n$stderr"
    }

    Invoke-SmokeRequest -Path "/api/health" -MinLength 10
    Invoke-SmokeRequest -Path "/api/version" -MinLength 5
    Invoke-SmokeRequest -Path "/" -MinLength 1000
    Invoke-SmokeRequest -Path "/login" -MinLength 1000
    Invoke-SmokeRequest -Path "/static/app.js" -MinLength 1000
    Invoke-SmokeRequest -Path "/static/style.css" -MinLength 1000

    $dbPath = Join-Path $LocalAppDataRoot "Odysseus\data\app.db"
    if (-not (Test-Path -LiteralPath $dbPath -PathType Leaf)) {
        $stdout = if (Test-Path $stdoutPath) { Get-Content $stdoutPath -Raw } else { "" }
        $stderr = if (Test-Path $stderrPath) { Get-Content $stderrPath -Raw } else { "" }
        throw "Packaged Windows app did not create writable SQLite database at $dbPath.`nSTDOUT:`n$stdout`nSTDERR:`n$stderr"
    }

    Write-Host "Smoke writable data passed: $dbPath"
    Write-Host "Smoke test passed: isolated Windows package launch from $InstallRoot"
}
finally {
    if ($proc -and -not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force
        $proc.WaitForExit()
    }
}
