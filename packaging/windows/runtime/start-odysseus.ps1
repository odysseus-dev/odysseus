#Requires -Version 5.1
. "$PSScriptRoot\common.ps1"

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $Script:LogsRoot | Out-Null
Start-Transcript -Path (Join-Path $Script:LogsRoot "start.log") -Append | Out-Null

try {
    Initialize-OdysseusRuntime
    Assert-DockerReady

    Write-OdysseusStep "Starting Docker services"
    [void](Invoke-DockerCompose up -d --build)
    if ($LASTEXITCODE -ne 0) {
        Pause-OnError "docker compose up failed. Open View Odysseus Logs for details."
    }

    if (Wait-OdysseusReady) {
        Write-Host ""
        Write-Host "Odysseus is ready at http://127.0.0.1:7000" -ForegroundColor Green
        Open-Odysseus
    } else {
        Write-Host ""
        Write-Host "Odysseus is still starting. Open http://127.0.0.1:7000 in a minute, or use View Odysseus Logs." -ForegroundColor Yellow
    }
} catch {
    Pause-OnError $_.Exception.Message
} finally {
    Stop-Transcript | Out-Null
}
