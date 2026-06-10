#Requires -Version 5.1
Set-StrictMode -Version Latest

$Script:InstallRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Script:LocalRoot = Join-Path $env:LOCALAPPDATA "Odysseus"
$Script:AppRoot = Join-Path $Script:LocalRoot "app"
$Script:DataRoot = Join-Path $Script:LocalRoot "data"
$Script:LogsRoot = Join-Path $Script:LocalRoot "logs"
$Script:ComposePath = Join-Path $Script:LocalRoot "docker-compose.yml"
$Script:EnvPath = Join-Path $Script:LocalRoot ".env"
$Script:Url = "http://127.0.0.1:7000/login"

function Write-OdysseusStep {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host ""
    Write-Host ("==> " + $Message) -ForegroundColor Cyan
}

function Pause-OnError {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host ""
    Write-Host ("ERROR: " + $Message) -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

function Test-DockerCompose {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) { return $false }

    & docker version *> $null
    if ($LASTEXITCODE -ne 0) { return $false }

    & docker compose version *> $null
    return $LASTEXITCODE -eq 0
}

function Start-DockerDesktopIfAvailable {
    $candidates = @(
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
        "${env:ProgramFiles(x86)}\Docker\Docker\Docker Desktop.exe",
        "$env:LOCALAPPDATA\Docker\Docker Desktop.exe"
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            Start-Process -FilePath $candidate -WindowStyle Hidden
            return $true
        }
    }
    return $false
}

function Assert-DockerReady {
    if (Test-DockerCompose) { return }

    Write-OdysseusStep "Starting Docker Desktop"
    [void](Start-DockerDesktopIfAvailable)

    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 2
        if (Test-DockerCompose) { return }
    }

    Pause-OnError "Docker Desktop with Docker Compose is required. Install or start Docker Desktop, finish any WSL2 or reboot prompts, then run Start Odysseus again."
}

function Copy-OdysseusPayload {
    New-Item -ItemType Directory -Force -Path $Script:LocalRoot, $Script:AppRoot, $Script:DataRoot, $Script:LogsRoot | Out-Null

    $excludeDirs = @(
        "data", "logs", "venv", ".venv", ".git", ".github", ".pytest_cache",
        "node_modules", "dist", "build", "__pycache__", "tests"
    )
    $excludeFiles = @(".env", "*.pyc", "*.pyo", "*.log", "*.db", "*.sqlite", "*.sqlite3")

    $args = @(
        $Script:InstallRoot,
        $Script:AppRoot,
        "/MIR",
        "/NFL",
        "/NDL",
        "/NJH",
        "/NJS",
        "/NP",
        "/XD"
    ) + $excludeDirs + @("/XF") + $excludeFiles

    & robocopy @args | Out-Null
    if ($LASTEXITCODE -gt 7) {
        Pause-OnError "Failed to copy Odysseus runtime files to $Script:AppRoot."
    }
}

function Initialize-OdysseusRuntime {
    Write-OdysseusStep "Preparing local runtime files"
    Copy-OdysseusPayload

    $template = Join-Path $Script:InstallRoot "runtime\docker-compose.yml.template"
    if (-not (Test-Path $template)) {
        Pause-OnError "Missing Docker Compose template: $template"
    }
    Copy-Item -Force -Path $template -Destination $Script:ComposePath

    if (-not (Test-Path $Script:EnvPath)) {
        $example = Join-Path $Script:AppRoot ".env.example"
        if (Test-Path $example) {
            Copy-Item -Path $example -Destination $Script:EnvPath
        } else {
            New-Item -ItemType File -Path $Script:EnvPath | Out-Null
        }
    }
}

function Invoke-DockerCompose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & docker compose --project-name odysseus --file $Script:ComposePath @Arguments
    return $LASTEXITCODE
}

function Wait-OdysseusReady {
    Write-OdysseusStep "Waiting for Odysseus"
    for ($i = 0; $i -lt 180; $i++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Script:Url -TimeoutSec 2 -ErrorAction Stop
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return $true
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    return $false
}

function Open-Odysseus {
    Start-Process "http://127.0.0.1:7000"
}
