param(
    [Parameter(Mandatory = $true)][string]$ExePath,
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 7000,
    [int]$StartupTimeoutSec = 90
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
    throw "Executable not found: $ExePath"
}

$stdoutPath = Join-Path $PWD "odysseus-windows-smoke.stdout.log"
$stderrPath = Join-Path $PWD "odysseus-windows-smoke.stderr.log"
Remove-Item -LiteralPath $stdoutPath, $stderrPath -ErrorAction SilentlyContinue

$env:ODYSSEUS_HOST = $BindHost
$env:ODYSSEUS_PORT = [string]$Port
$env:AUTH_ENABLED = "false"
$env:LOCALHOST_BYPASS = "true"
$env:ODYSSEUS_INPROCESS_TASKS = "0"
$env:ODYSSEUS_INPROCESS_POLLERS = "0"

$proc = Start-Process -FilePath $ExePath `
    -PassThru `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath

try {
    $deadline = (Get-Date).AddSeconds($StartupTimeoutSec)
    $healthUrl = "http://$BindHost`:$Port/api/health"
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

    if (-not $started) {
        $stdout = if (Test-Path $stdoutPath) { Get-Content $stdoutPath -Raw } else { "" }
        $stderr = if (Test-Path $stderrPath) { Get-Content $stderrPath -Raw } else { "" }
        throw "Windows package did not become healthy at $healthUrl within $StartupTimeoutSec seconds.`nSTDOUT:`n$stdout`nSTDERR:`n$stderr"
    }

    Write-Host "Smoke test passed: $healthUrl"
}
finally {
    if ($proc -and -not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force
        $proc.WaitForExit()
    }
}
