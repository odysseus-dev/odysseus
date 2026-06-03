#Requires -Version 5.1
. "$PSScriptRoot\common.ps1"

if (-not (Test-Path $Script:ComposePath)) {
    Write-Host "Odysseus has not been started yet."
    Write-Host "Runtime folder: $Script:LocalRoot"
    exit 0
}

if (-not (Test-DockerCompose)) {
    Write-Host "Docker Compose is not available. Showing local log folder instead:"
    Write-Host $Script:LogsRoot
    Start-Process $Script:LogsRoot
    exit 0
}

Write-Host "Tailing Odysseus Docker logs. Press Ctrl+C to stop."
& docker compose --project-name odysseus --file $Script:ComposePath logs --follow --tail 200
