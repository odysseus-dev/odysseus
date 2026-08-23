#Requires -Version 5.1
param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$odysseusRoot = $PSScriptRoot
$ollamaBase = "http://127.0.0.1:11434"
$odysseusBase = "http://127.0.0.1:7000"

function Test-HttpReady([string]$Url) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    } catch {
        return $false
    }
}

function Wait-HttpReady([string]$Url, [int]$TimeoutSeconds) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if (Test-HttpReady $Url) { return $true }
        Start-Sleep -Milliseconds 400
    } while ([DateTime]::UtcNow -lt $deadline)
    return $false
}

function Find-OllamaExecutable {
    $command = Get-Command "ollama.exe" -ErrorAction SilentlyContinue
    if ($command -and $command.Source -and (Test-Path -LiteralPath $command.Source)) {
        return $command.Source
    }

    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"),
        (Join-Path $env:ProgramFiles "Ollama\ollama.exe")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    return $null
}

function Get-McpSdkVersion([string]$PythonExecutable) {
    try {
        $version = & $PythonExecutable -c "import importlib.metadata as m; print(m.version('mcp'))" 2>$null
        if ($LASTEXITCODE -eq 0) { return ($version | Select-Object -Last 1).Trim() }
    } catch {}
    return $null
}

function Ensure-CompatibleMcpSdk([string]$PythonExecutable) {
    $version = Get-McpSdkVersion $PythonExecutable
    if ($version -and $version.StartsWith("1.")) { return }

    Write-Host "Repairing Odysseus MCP tools dependency..."
    & $PythonExecutable -m pip install --disable-pip-version-check "mcp<2"
    $version = Get-McpSdkVersion $PythonExecutable
    if ($LASTEXITCODE -ne 0 -or -not $version -or -not $version.StartsWith("1.")) {
        throw "The required MCP SDK v1 could not be installed. Run venv\Scripts\python.exe -m pip install -r requirements.txt and retry."
    }
}

try {
    $venvPython = Join-Path $odysseusRoot "venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython)) {
        throw "Odysseus' Python environment is missing. Run launch-windows.ps1 once to create it."
    }
    Ensure-CompatibleMcpSdk $venvPython

    if (-not (Test-HttpReady "$ollamaBase/api/version")) {
        $ollamaExe = Find-OllamaExecutable
        if (-not $ollamaExe) {
            throw "Ollama is not installed. Install it from https://ollama.com/download/windows and launch Odysseus again."
        }

        Write-Host "Starting Ollama..."
        Start-Process -FilePath $ollamaExe -ArgumentList @("serve") -WindowStyle Hidden | Out-Null
        if (-not (Wait-HttpReady "$ollamaBase/api/version" 45)) {
            throw "Ollama did not become ready on 127.0.0.1:11434 within 45 seconds."
        }
    }

    if (-not (Test-HttpReady $odysseusBase)) {
        Write-Host "Starting Odysseus..."
        $stdoutLog = Join-Path $odysseusRoot "odysseus-server.log"
        $stderrLog = Join-Path $odysseusRoot "odysseus-server-error.log"
        Start-Process `
            -FilePath $venvPython `
            -ArgumentList @("-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "7000") `
            -WorkingDirectory $odysseusRoot `
            -RedirectStandardOutput $stdoutLog `
            -RedirectStandardError $stderrLog `
            -WindowStyle Hidden | Out-Null
        if (-not (Wait-HttpReady $odysseusBase 45)) {
            throw "Odysseus did not become ready on 127.0.0.1:7000 within 45 seconds. Check odysseus-server-error.log."
        }
    }

    if (-not $NoBrowser) {
        Start-Process $odysseusBase | Out-Null
    }
} catch {
    Write-Host ""
    Write-Host ("Odysseus could not start: " + $_.Exception.Message) -ForegroundColor Red
    Write-Host ""
    if (-not $NoBrowser) {
        Read-Host "Press Enter to close"
    }
    exit 1
}
