#Requires -Version 5.1
$ErrorActionPreference = "Stop"

function Test-DockerCompose {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) { return $false }
    & docker version *> $null
    if ($LASTEXITCODE -ne 0) { return $false }
    & docker compose version *> $null
    return $LASTEXITCODE -eq 0
}

if (Test-DockerCompose) {
    Write-Host "Docker Desktop and Docker Compose are available."
    exit 0
}

$winget = Get-Command winget -ErrorAction SilentlyContinue
if (-not $winget) {
    Write-Host "Docker Desktop is required, and winget was not found."
    Write-Host "Install Docker Desktop from https://www.docker.com/products/docker-desktop/ and run this setup again."
    exit 1603
}

Write-Host "Docker Desktop was not detected. Attempting install through winget."
& winget install -e --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements
$code = $LASTEXITCODE

if ($code -eq 0 -or $code -eq 3010) {
    Write-Host "Docker Desktop install command completed. Finish any Docker Desktop prompts or reboot if requested."
    exit 0
}

Write-Host "Docker Desktop install failed with exit code $code."
Write-Host "Install Docker Desktop manually, then run Odysseus Setup again."
exit 1603
