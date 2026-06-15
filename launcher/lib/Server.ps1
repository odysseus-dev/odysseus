function Start-OdysseusServer {
    param(
        $Context,
        [scriptblock]$OnLog
    )

    if (-not (Test-Path $Context.VenvPython)) {
        throw "Virtual environment not found at $($Context.VenvPython)"
    }

    $logDir = Split-Path $Context.ServerLog -Parent
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }

    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $Context.ServerLog -Value "`n--- launcher start $stamp ---`n" -Encoding UTF8

    $uvicornArgs = "-m uvicorn app:app --host $($Context.BindHost) --port $($Context.Port)"
    $cmd = 'cd /d "{0}" && "{1}" {2} >> "{3}" 2>&1' -f `
        $Context.RepoRoot, $Context.VenvPython, $uvicornArgs, $Context.ServerLog

    $proc = Start-Process -FilePath 'cmd.exe' `
        -ArgumentList '/c', $cmd `
        -WorkingDirectory $Context.RepoRoot `
        -WindowStyle Hidden `
        -PassThru

    $Context.ServerProcess = $proc
    & $OnLog ("Server started (pid {0}). Log: {1}" -f $proc.Id, $Context.ServerLog)
    return $proc
}

function Stop-OdysseusServer {
    param(
        $Context,
        [scriptblock]$OnLog
    )

    if ($Context.ServerProcess -and -not $Context.ServerProcess.HasExited) {
        try {
            Stop-Process -Id $Context.ServerProcess.Id -Force -ErrorAction Stop
            & $OnLog 'Server stopped.'
        } catch {
            & $OnLog ("Failed to stop server: {0}" -f $_.Exception.Message) 'WARN'
        }
    } elseif (Test-OdysseusServerListening -Context $Context) {
        & $OnLog 'Server is running externally - close that process manually if needed.' 'WARN'
    }
    $Context.ServerProcess = $null
}

function Wait-OdysseusServerReady {
    param(
        $Context,
        [int]$TimeoutSec = 120,
        [scriptblock]$OnLog
    )

    $elapsed = 0
    while ($elapsed -lt $TimeoutSec) {
        if ($Context.ServerProcess -and $Context.ServerProcess.HasExited) {
            & $OnLog 'Server process exited before becoming ready.' 'ERROR'
            return $false
        }

        $health = Invoke-OdysseusHttpGet -Url $Context.HealthUrl -TimeoutSec 2
        if ($health -and $health.StatusCode -lt 400) {
            & $OnLog 'Server is ready.'
            return $true
        }

        Start-Sleep -Seconds 1
        $elapsed++
        if ($elapsed % 10 -eq 0) {
            & $OnLog ("Still waiting... {0}s / {1}s" -f $elapsed, $TimeoutSec)
        }
    }

    & $OnLog 'Timed out waiting for server readiness.' 'ERROR'
    return $false
}

function Test-OdysseusServerListening {
    param($Context)
    return Test-OdysseusTcpPort -HostName '127.0.0.1' -Port $Context.Port
}