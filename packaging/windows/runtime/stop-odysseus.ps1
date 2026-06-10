#Requires -Version 5.1
. "$PSScriptRoot\common.ps1"

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $Script:LogsRoot | Out-Null
Start-Transcript -Path (Join-Path $Script:LogsRoot "stop.log") -Append | Out-Null

try {
    if (-not (Test-Path $Script:ComposePath)) {
        Write-Host "No Odysseus runtime has been initialized yet."
        exit 0
    }
    Assert-DockerReady
    Write-OdysseusStep "Stopping Docker services"
    [void](Invoke-DockerCompose down)
    if ($LASTEXITCODE -ne 0) {
        Pause-OnError "docker compose down failed."
    }
    Write-Host "Odysseus stopped. User data was preserved at $Script:LocalRoot." -ForegroundColor Green
} catch {
    Pause-OnError $_.Exception.Message
} finally {
    Stop-Transcript | Out-Null
}
