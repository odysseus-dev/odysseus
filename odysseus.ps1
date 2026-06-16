#Requires -Version 5.1
<#
  Unified Odysseus Windows launcher.

  Preferred usage:
    .\odysseus run --launch native --host 127.0.0.1 --port 7000
    .\odysseus # Same as above (uses defaults)
    .\odysseus update --launch docker
    .\odysseus add-to-path
    .\odysseus remove-from-path
    .\odysseus add-autostart
    .\odysseus remove-autostart

  Backward-compatible aliases are accepted via legacy flags.
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet("run", "update", "add-to-path", "remove-from-path", "add-autostart", "remove-autostart", "help")]
    [string]$Command = "run",

    [Parameter()]
    [ValidateSet("run", "update", "add-to-path", "remove-from-path", "add-autostart", "remove-autostart", "help")]
    [string]$SubCommand,

    [ValidateSet("docker", "docker-nvidia", "docker-amd", "native", "standalone")]
    [string]$Launch = "docker",

    [switch]$ForceBuild,

    [int]$Port = 7000,

    [Alias("Host")]
    [ValidatePattern('^(localhost|(?=.{1,253}$)([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*|(?:\d{1,3}\.){3}\d{1,3})$')]
    [string]$BindHost = "127.0.0.1",

    # Legacy/compatibility flags
    [switch]$Update,
    [switch]$Help,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Step($msg) {
    Write-Host ""
    Write-Host ("==> " + $msg) -ForegroundColor Cyan
}

function Fail($msg) {
    Write-Host ""
    Write-Host ("ERROR: " + $msg) -ForegroundColor Red
    exit 1
}

function Show-CommandHelp {
    param (
        [string]$Name,
        [string]$Description,
        [string[]]$Options,
        [string[]]$Examples
    )
@"
Odysseus Windows launcher

Usage:
  .\odysseus $Name [options]

Description:
  $Description

Options:
$($Options -join "`n")

Examples:
$($Examples -join "`n")
"@ | Write-Host
}

function Show-NoOptionsHelp {
    param (
        [string]$Name,
        [string]$Description
    )

    Show-CommandHelp -Name $Name `
        -Description $Description `
        -Options @(
            "(none)"
        ) `
        -Examples @(
            ".\odysseus $Name"
        )
}

function Show-Help {
    param([string]$SubCommand)

    switch ($SubCommand) {

        "run" {
            Show-CommandHelp -Name "run" `
                -Description "Create/refresh environment and run the app" `
                -Options @(
                    "-Launch native|docker|docker-nvidia|docker-amd|standalone   Default: native",
                    "-Host <host>                                                 Default: 127.0.0.1",
                    "-Port <port>                                                 Default: 7000",
                    "--force-build                                                Force a rebuild of the standalone launcher"
                ) `
                -Examples @(
                    ".\odysseus run",
                    ".\odysseus run --launch docker --port 8080",
                    ".\odysseus run --launch standalone",
                    ".\odysseus run --launch standalone --force-build"
                )
        }

        "update" {
            Show-CommandHelp -Name "update" `
                -Description "Pull latest changes and refresh dependencies" `
                -Options @(
                    "-Launch native|docker|docker-nvidia|docker-amd|standalone   Default: native"
                ) `
                -Examples @(
                    ".\odysseus update",
                    ".\odysseus update --launch docker",
                    ".\odysseus update --launch standalone"
                )
        }

        "add-autostart" {
            Show-CommandHelp -Name "add-autostart" `
                -Description "Register Windows Scheduled Task at user logon" `
                -Options @(
                    "--launch native|docker|docker-nvidia|docker-amd     Default: native",
                    "--host <host>                                       Default: 127.0.0.1",
                    "--port <port>                                       Default: 7000"
                ) `
                -Examples @(
                    ".\odysseus add-autostart",
                    ".\odysseus add-autostart --host 0.0.0.0 --port 8080"
                )
        }

        "remove-autostart" {
            Show-NoOptionsHelp -Name $SubCommand `
                -Description "Remove Windows Scheduled Task"
        }

        "add-to-path" {
            Show-NoOptionsHelp -Name $SubCommand `
                -Description "Add this repo path to User PATH"
        }

        "remove-from-path" {
            Show-NoOptionsHelp -Name $SubCommand `
                -Description "Remove this repo path from User PATH"
        }

        default {
            if ($SubCommand -and $SubCommand -ne "help") {
                Write-Host "Unknown command for help: $SubCommand" -ForegroundColor Red
            }

@"
Odysseus Windows launcher

Usage:
  .\odysseus <command> [options]

Commands:
  run               Run the app
  update            Update app and dependencies
  add-to-path       Add repo to PATH
  remove-from-path  Remove repo from PATH
  add-autostart     Enable autostart
  remove-autostart  Disable autostart
  help              Show help

Get command-specific help:
  .\odysseus help run
  .\odysseus run help
"@ | Write-Host
        }
    }
}

function Find-GitBash {
    $cmd = Get-Command bash -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $roots = @()
    foreach ($name in @("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LocalAppData")) {
        $base = [Environment]::GetEnvironmentVariable($name)
        if ($base) { $roots += (Join-Path $base "Git") }
    }
    $roots += @("C:\Program Files\Git", "C:\Program Files (x86)\Git")

    foreach ($root in ($roots | Select-Object -Unique)) {
        foreach ($relative in @("bin\bash.exe", "usr\bin\bash.exe")) {
            $candidate = Join-Path $root $relative
            if (Test-Path $candidate) { return $candidate }
        }
    }
    return $null
}

function Get-PythonVersionText($launcher, $launcherArgs) {
    try {
        return (& $launcher @launcherArgs -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null).Trim()
    } catch {
        return $null
    }
}

function Resolve-Python {
    Write-Step "Checking for Python"

    $pyExe = $null
    $pyArgs = @()
    $pyVersion = $null

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        foreach ($v in @("-3.13", "-3.12", "-3.11")) {
            $ver = Get-PythonVersionText $pyLauncher.Source @($v)
            if ($ver) {
                $pyExe = $pyLauncher.Source
                $pyArgs = @($v)
                $pyVersion = $ver
                break
            }
        }
    }

    if (-not $pyExe) {
        $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
        if ($pythonCmd) {
            $ver = Get-PythonVersionText $pythonCmd.Source @()
            if ($ver) {
                $versionParts = $ver.Split('.')
                $major = [int]$versionParts[0]
                $minor = [int]$versionParts[1]
                if ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11)) {
                    $pyExe = $pythonCmd.Source
                    $pyVersion = $ver
                }
            }
        }
    }

    if (-not $pyExe) {
        Fail "Couldn't find Python 3.11+ for Windows setup. Install Python 3.11+ from https://www.python.org/downloads/, then re-run this script."
    }

    $pythonLabel = ("Using Python {0}: {1} {2}" -f $pyVersion, $pyExe, ($pyArgs -join ' ')).TrimEnd()
    Write-Host $pythonLabel

    return @{
        Exe = $pyExe
        Args = $pyArgs
    }
}

function Ensure-NativeRuntime {
    $py = Resolve-Python
    $venvPy = Join-Path $PSScriptRoot "venv\Scripts\python.exe"

    if (-not (Test-Path $venvPy)) {
        Write-Step "Creating virtual environment (venv)"
        & $py.Exe @($py.Args) -m venv venv
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) { Fail "Failed to create the virtual environment." }
    } else {
        Write-Host "venv already exists - skipping creation."
    }

    Write-Step "Installing dependencies"
    & $venvPy -m pip install --upgrade pip --quiet | Out-Host
    & $venvPy -m pip install -r requirements.txt | Out-Host
    if ($LASTEXITCODE -ne 0) { Fail "Dependency install failed. Scroll up for the pip error." }

    Write-Step "Running first-time setup"
    & $venvPy setup.py | Out-Host
    if ($LASTEXITCODE -ne 0) { Fail "setup.py failed." }

    if (-not (Find-GitBash)) {
        Write-Host ""
        Write-Host "NOTE: Git Bash (bash.exe) was not found on PATH." -ForegroundColor Yellow
        Write-Host "      Core app works without it. For full Cookbook/agent-shell parity:" -ForegroundColor Yellow
        Write-Host "      https://git-scm.com/download/win" -ForegroundColor Yellow
    }

    return $venvPy
}

function Build-StandaloneLauncher {
    $venvPy = Ensure-NativeRuntime

    Write-Step "Installing PyInstaller and GUI dependencies"
    & $venvPy -m pip install pyinstaller pystray Pillow --quiet | Out-Host
    if ($LASTEXITCODE -ne 0) { Fail "Failed to install build dependencies." }

    Write-Step "Cleaning previous build artifacts"
    Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

    Write-Step "Building standalone launcher"
    $dataArgs = @(
        "--add-data", "static;static",
        "--add-data", "scripts;scripts",
        "--add-data", "mcp_servers;mcp_servers",
        "--add-data", "services/hwfit/data;services/hwfit/data",
        "--add-data", "config;config",
        "--add-data", ".env.example;.env.example"
    )
    & $venvPy -m PyInstaller --noconfirm --clean --onedir --noconsole `
        --icon=static/icon.ico --name Odysseus @dataArgs launcher.py | Out-Host
    if ($LASTEXITCODE -ne 0) { Fail "PyInstaller build failed." }

    Write-Host ""
    Write-Host "Build complete." -ForegroundColor Green
    Write-Host "Launcher: $PSScriptRoot\dist\Odysseus\Odysseus.exe" -ForegroundColor Green
}

function Resolve-DockerComposeArgs {
    param([string]$RequestedLaunch)

    if ($RequestedLaunch -eq "docker-amd") {
        Fail "docker-amd is not supported on Windows. ROCm Docker requires Linux kernel devices (/dev/kfd). Use: .\odysseus run -Launch native"
    }

    if ($RequestedLaunch -eq "docker-nvidia") {
        return @("-f", "docker-compose.yml", "-f", "docker/gpu.nvidia.yml")
    }

    if ($RequestedLaunch -eq "docker") {
        $hasNvidia = $false

        $nvidiaCmd = Get-Command nvidia-smi -ErrorAction SilentlyContinue
        if ($nvidiaCmd) {
            & $nvidiaCmd.Source 1>$null 2>$null
            if ($LASTEXITCODE -eq 0) { $hasNvidia = $true }
        }

        if ($hasNvidia) {
            Write-Host "NVIDIA GPU detected - using docker/gpu.nvidia.yml"
            return @("-f", "docker-compose.yml", "-f", "docker/gpu.nvidia.yml")
        }

        Write-Host "No NVIDIA Docker runtime detected - using CPU docker-compose.yml"
    }

    return @("-f", "docker-compose.yml")
}

function Invoke-Run {
    param([string]$RequestedLaunch, [string]$BindHost, [int]$BindPort, [bool]$ForceBuild = $false)

    if ($RequestedLaunch -eq "standalone") {
        $launcherExe = Join-Path $PSScriptRoot "dist\Odysseus\Odysseus.exe"

        if ($ForceBuild -or -not (Test-Path $launcherExe)) {
            if (-not (Test-Path $launcherExe)) {
                Write-Host "Standalone launcher not found - building..."
            }
            Build-StandaloneLauncher
        }

        $env:APP_BIND = $BindHost
        $env:APP_PORT = $BindPort
        Write-Step ("Odysseus available at http://{0}:{1}" -f $BindHost, $BindPort)
        Write-Host "Starting standalone launcher. The app will open in your browser."
        Write-Host ""
        & $launcherExe
        return
    }

    if ($RequestedLaunch -eq "native") {
        $venvPy = Ensure-NativeRuntime
        Write-Step ("Odysseus available at http://{0}:{1}" -f $BindHost, $BindPort)
        Write-Host "Press Ctrl+C to stop."
        Write-Host ""

        & $venvPy -m uvicorn app:app --host $BindHost --port $BindPort
        
        return
    }
    
    Write-Step "Starting Docker deployment"

    # Only set APP_BIND if user explicitly wants it network-accessible
    if ($BindHost -eq "0.0.0.0") {
        $env:APP_BIND = "0.0.0.0"
    } elseif ($BindHost -ne "127.0.0.1") {
        Fail "Docker launch on Windows supports -Host values of 127.0.0.1 or 0.0.0.0 only. Received: $BindHost"
    }

    $env:APP_PORT = $BindPort

    $composeArgs = Resolve-DockerComposeArgs -RequestedLaunch $RequestedLaunch

    docker compose @composeArgs up -d

    if ($LASTEXITCODE -ne 0) { Fail "docker compose up failed." }

    Write-Host "Docker services started."
    Write-Step ("Odysseus available at http://{0}:{1}" -f $BindHost, $BindPort)
}

function Invoke-Update {
    param([string]$RequestedLaunch)

    Write-Step "Pulling latest code"
    git pull --ff-only
    if ($LASTEXITCODE -ne 0) { Fail "git pull --ff-only failed. Resolve local branch state and retry." }

    $nativeVenvPy = $null
    if ($RequestedLaunch -eq "native") {
        $nativeVenvPy = Ensure-NativeRuntime
        Write-Step "Refreshing dependencies"
        & $nativeVenvPy -m pip install -r requirements.txt | Out-Host
        if ($LASTEXITCODE -ne 0) { Fail "Dependency refresh failed." }

        return
    }

    if ($RequestedLaunch -eq "standalone") {
        Build-StandaloneLauncher
        return
    }

    Write-Step "Rebuilding Docker services"

    $composeArgs = Resolve-DockerComposeArgs -RequestedLaunch $RequestedLaunch

    docker compose @composeArgs up -d --build
    
    if ($LASTEXITCODE -ne 0) { Fail "docker compose up -d --build failed." }

    Write-Step "Pruning dangling Docker images"
    docker image prune -f
    if ($LASTEXITCODE -ne 0) { Fail "docker image prune failed." }
}

function Add-ToPath {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @()
    if ($userPath) { $parts = $userPath.Split(';') | Where-Object { $_ -and $_.Trim() } }

    foreach ($p in $parts) {
        if ($p.TrimEnd('\\').ToLowerInvariant() -eq $PSScriptRoot.TrimEnd('\\').ToLowerInvariant()) {
            Write-Host "Path already present in User PATH: $PSScriptRoot"
            return
        }
    }

    $newPath = if ($userPath) { "$userPath;$PSScriptRoot" } else { $PSScriptRoot }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Write-Host "Added to User PATH: $PSScriptRoot"
    Write-Host "Open a new shell to pick up the updated PATH."
}

function Remove-FromPath {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $userPath) {
        Write-Host "User PATH is empty; nothing to remove."
        return
    }

    $parts = $userPath.Split(';') | Where-Object { $_ -and $_.Trim() }
    $filtered = @()
    foreach ($p in $parts) {
        if ($p.TrimEnd('\\').ToLowerInvariant() -ne $PSScriptRoot.TrimEnd('\\').ToLowerInvariant()) {
            $filtered += $p
        }
    }

    [Environment]::SetEnvironmentVariable("Path", ($filtered -join ';'), "User")
    Write-Host "Removed from User PATH (if present): $PSScriptRoot"
}

function Add-Autostart {
    $taskName = "OdysseusLauncher"
    $pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue)
    $shellExe = if ($pwsh) { $pwsh.Source } else { "powershell.exe" }

    $scriptPath = Join-Path $PSScriptRoot "odysseus.ps1"
    $argString = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$scriptPath`" run -Launch `"$Launch`" -BindHost `"$BindHost`" -Port `"$Port`""
    $taskRun = "`"$shellExe`" $argString"

    schtasks /Create /TN $taskName /SC ONLOGON /TR $taskRun /F | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "Failed to register scheduled task '$taskName'." }
    Write-Host "Autostart task registered: $taskName"
}

function Remove-Autostart {
    $taskName = "OdysseusLauncher"
    schtasks /Delete /TN $taskName /F | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Autostart task '$taskName' not found (or already removed)."
        return
    }
    Write-Host "Autostart task removed: $taskName"
}

# Map legacy Update flag to Command
if ($Update) { $Command = "update" }

# Parse GNU-style flags from RemainingArgs
if ($RemainingArgs) {
    for ($i = 0; $i -lt $RemainingArgs.Length; $i++) {
        $token = $RemainingArgs[$i]

        # Long option with value: --name value
        if ($token -match '^--([A-Za-z0-9][A-Za-z0-9-]*)$') {
            $opt = $matches[1].ToLower()

            switch ($opt) {
                'launch' {
                    if ($i + 1 -lt $RemainingArgs.Length) {
                        $Launch = $RemainingArgs[$i + 1]
                        $i++
                    }
                }
                'port' {
                    if ($i + 1 -lt $RemainingArgs.Length) {
                        $Port = [int]$RemainingArgs[$i + 1]
                        $i++
                    }
                }
                'host' {
                    if ($i + 1 -lt $RemainingArgs.Length) {
                        $BindHost = $RemainingArgs[$i + 1]
                        $i++
                    }
                }
                'force-build' { $ForceBuild = $true }
                'help' { $Help = $true }
                default {
                    Write-Host "Unknown long option: --$opt"
                }
            }
        }
        # Long option with equals: --name=value
        elseif ($token -match '^--([A-Za-z0-9][A-Za-z0-9-]*)=(.+)$') {
            $opt = $matches[1].ToLower()
            $val = $matches[2]

            switch ($opt) {
                'launch' { $Launch = $val }
                'port' { $Port = [int]$val }
                'host' { $BindHost = $val }
                default { Write-Host "Unknown long option: --$opt" }
            }
        }
        elseif (@("help", "-h", "/h", "/?") -contains $token.ToLower()) {
             $Help = $true
        }
        elseif ($Command -eq "help" -and -not $SubCommand) {
             $SubCommand = $token.ToLower()
        }
        else {
            # Non-option leftover; you can handle positional extras here if needed
            Write-Host "Leftover token: $token"
        }
    }
}

# Handle help flag
if ($Help) { 
    if($Command -ne "help") { 
        $SubCommand = $Command
        $Command = "help"
    }
}

switch ($Command) {
    "run" {
        Invoke-Run -RequestedLaunch $Launch -BindHost $BindHost -BindPort $Port -ForceBuild $ForceBuild
    }
    "update" {
        Invoke-Update -RequestedLaunch $Launch
    }
    "add-to-path" {
        Add-ToPath
    }
    "remove-from-path" {
        Remove-FromPath
    }
    "add-autostart" {
        Add-Autostart
    }
    "remove-autostart" {
        Remove-Autostart
    }
    "help" {
        Show-Help $SubCommand
    }
    default {
        Show-Help $SubCommand
        exit 1
    }
}