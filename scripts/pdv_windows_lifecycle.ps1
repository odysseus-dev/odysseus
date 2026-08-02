#Requires -Version 5.1
<#
.SYNOPSIS
  Native Windows lifecycle wrapper for the loopback-only PDV adapter instance.

.DESCRIPTION
  Check validates configuration without opening a listener. Start launches a
  hidden Uvicorn child and records its PID under the ignored data directory.
  Stop terminates only a matching Uvicorn process started from this checkout.
#>
param(
    [ValidateSet("Check", "Start", "Stop", "Restart")]
    [string]$Action = "Check",
    [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$AdapterKeyFile = $env:ODYSSEUS_PDV_ADAPTER_KEY_FILE,
    [string]$ExecutionOsUrl = $env:PDV_EXECUTION_OS_URL,
    [string]$PythonExecutable = "",
    [ValidateRange(1, 65535)]
    [int]$Port = 7000,
    [switch]$Json
)

$ErrorActionPreference = "Stop"
$reservedPorts = @(11435, 11436)
$root = [System.IO.Path]::GetFullPath($RepositoryRoot)
$runtimeDir = Join-Path $root "data\pdv-integration-v1"
$logDir = Join-Path $runtimeDir "logs"
$pidFile = Join-Path $runtimeDir "odysseus.pid"
$errors = [System.Collections.Generic.List[string]]::new()
$processStarted = $false
$processStopped = $false
$processId = $null
$executionOsConfigured = -not [string]::IsNullOrWhiteSpace($ExecutionOsUrl)

if (-not (Test-Path -LiteralPath $root -PathType Container) -or
    -not (Test-Path -LiteralPath (Join-Path $root "app.py") -PathType Leaf)) {
    $errors.Add("Odysseus repository root")
}
if ($Port -in $reservedPorts) {
    $errors.Add("reserved model port")
}
if ($executionOsConfigured) {
    try {
        $executionUri = [uri]$ExecutionOsUrl
        if ($executionUri.Scheme -ne "http" -or $executionUri.Host -notin @("127.0.0.1", "localhost", "::1") -or $executionUri.Port -le 0) {
            $errors.Add("loopback PDV Execution OS URL")
        }
    } catch { $errors.Add("loopback PDV Execution OS URL") }
} else { $errors.Add("PDV Execution OS URL") }

$keyConfigured = -not [string]::IsNullOrWhiteSpace($AdapterKeyFile)
$keyReadable = $false
$keyAclRestricted = $false
if ($keyConfigured) {
    try {
        $keyInfo = Get-Item -LiteralPath $AdapterKeyFile -ErrorAction Stop
        $keyText = if (-not $keyInfo.PSIsContainer) { (Get-Content -LiteralPath $AdapterKeyFile -Raw -Encoding ascii).Trim() } else { "" }
        $keyReadable = -not $keyInfo.PSIsContainer -and $keyText -match '^[a-f0-9]{64}$'
        $keyAcl = Get-Acl -LiteralPath $AdapterKeyFile -ErrorAction Stop
        $currentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $rules = @($keyAcl.Access)
        $keyAclRestricted = $keyAcl.AreAccessRulesProtected -and $rules.Count -eq 1 -and
            -not $rules[0].IsInherited -and $rules[0].AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and
            $rules[0].IdentityReference.Value -eq $currentIdentity -and
            (($rules[0].FileSystemRights -band [System.Security.AccessControl.FileSystemRights]::FullControl) -eq [System.Security.AccessControl.FileSystemRights]::FullControl)
    } catch {
        $keyReadable = $false
        $keyAclRestricted = $false
    }
}
if (-not $keyConfigured) { $errors.Add("adapter key-file reference") }
elseif (-not $keyReadable) { $errors.Add("readable 32-byte hex adapter key file") }
elseif (-not $keyAclRestricted) { $errors.Add("ACL-restricted adapter key file") }

if (-not $PythonExecutable) {
    foreach ($candidate in @(
        (Join-Path $root ".venv\Scripts\python.exe"),
        (Join-Path $root "venv\Scripts\python.exe")
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $PythonExecutable = $candidate
            break
        }
    }
}
if (-not $PythonExecutable) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) { $PythonExecutable = $pythonCommand.Source }
}
if (-not $PythonExecutable -or -not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    $errors.Add("Python executable")
}

function New-Report {
    param([string]$RequestedAction)
    return [ordered]@{
        ok = $script:errors.Count -eq 0
        action = $RequestedAction
        bindHost = "127.0.0.1"
        port = $script:Port
        authEnabled = $true
        localhostBypass = $false
        adapterKeyReferenceConfigured = $script:keyConfigured
        adapterKeyReadable = $script:keyReadable
        adapterKeyAclRestricted = $script:keyAclRestricted
        executionOsConfigured = $script:executionOsConfigured
        reservedPorts = @($script:reservedPorts)
        portsTouched = @($(if ($script:processStarted) { $script:Port }))
        processStarted = $script:processStarted
        processStopped = $script:processStopped
        processId = $script:processId
        errors = @($script:errors)
    }
}

function Write-ReportAndExit {
    param([int]$ExitCode)
    $report = New-Report $Action
    if ($Json) { $report | ConvertTo-Json -Depth 5 }
    else { $report | Format-List }
    exit $ExitCode
}

if ($Action -eq "Check") {
    Write-ReportAndExit $(if ($errors.Count -eq 0) { 0 } else { 1 })
}

if ($Action -eq "Restart") {
    $stopOutput = @(& $PSCommandPath -Action Stop -RepositoryRoot $root -AdapterKeyFile $AdapterKeyFile -ExecutionOsUrl $ExecutionOsUrl -PythonExecutable $PythonExecutable -Port $Port -Json)
    $stopExitCode = $LASTEXITCODE
    if ($stopExitCode -ne 0) { $errors.Add("existing PDV Odysseus process stopped for restart"); Write-ReportAndExit 1 }
    try { $stopReport = ($stopOutput -join [Environment]::NewLine) | ConvertFrom-Json } catch { $stopReport = $null }
    if (-not $stopReport -or -not $stopReport.processStopped) { $errors.Add("valid stop receipt for restart"); Write-ReportAndExit 1 }

    $startOutput = @(& $PSCommandPath -Action Start -RepositoryRoot $root -AdapterKeyFile $AdapterKeyFile -ExecutionOsUrl $ExecutionOsUrl -PythonExecutable $PythonExecutable -Port $Port -Json)
    $startExitCode = $LASTEXITCODE
    if ($startExitCode -ne 0) { $errors.Add("PDV Odysseus process started after restart"); Write-ReportAndExit 1 }
    try { $startReport = ($startOutput -join [Environment]::NewLine) | ConvertFrom-Json } catch { $startReport = $null }
    if (-not $startReport -or -not $startReport.processStarted) { $errors.Add("valid start receipt for restart"); Write-ReportAndExit 1 }

    $processStopped = $true
    $processStarted = $true
    $processId = [int]$startReport.processId
    Write-ReportAndExit 0
}

if ($Action -eq "Start") {
    if ($errors.Count -ne 0) { Write-ReportAndExit 1 }
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    if (Test-Path -LiteralPath $pidFile -PathType Leaf) {
        try { $existingReceipt = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json } catch { $existingReceipt = $null }
        $existingPid = if ($existingReceipt) { [int]$existingReceipt.pid } else { 0 }
        if ($existingPid -gt 0 -and (Get-Process -Id $existingPid -ErrorAction SilentlyContinue)) {
            $errors.Add("PDV Odysseus process is already running")
            Write-ReportAndExit 1
        }
    }

    $env:APP_BIND = "127.0.0.1"
    $env:AUTH_ENABLED = "true"
    $env:LOCALHOST_BYPASS = "false"
    $env:ODYSSEUS_PDV_ADAPTER_KEY_FILE = [System.IO.Path]::GetFullPath($AdapterKeyFile)
    $env:ODYSSEUS_DATA_DIR = $runtimeDir
    $env:PDV_EXECUTION_OS_URL = $ExecutionOsUrl.TrimEnd("/")
    $env:PDV_PROVIDER_GUARD_REQUIRED = "true"
    $env:PDV_ADAPTER_KEY_ACL_VERIFIED = "true"
    $stdout = Join-Path $logDir "stdout.log"
    $stderr = Join-Path $logDir "stderr.log"
    $arguments = @("-m", "uvicorn", "app:app", "--app-dir", $root, "--host", "127.0.0.1", "--port", [string]$Port)
    $startParameters = @{
        FilePath = $PythonExecutable
        ArgumentList = $arguments
        WorkingDirectory = $root
        WindowStyle = "Hidden"
        RedirectStandardOutput = $stdout
        RedirectStandardError = $stderr
        PassThru = $true
    }
    $process = Start-Process @startParameters
    $pidReceipt = [ordered]@{
        schemaVersion = 1
        pid = $process.Id
        startedAt = [DateTime]::UtcNow.ToString("o")
        executable = [System.IO.Path]::GetFullPath($PythonExecutable)
        repositoryRoot = $root
        bindHost = "127.0.0.1"
        port = $Port
    }
    $pidReceipt | ConvertTo-Json -Compress | Set-Content -LiteralPath $pidFile -Encoding utf8
    $live = $false
    foreach ($attempt in 1..40) {
        if ($process.HasExited) { break }
        try {
            $probe = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 1
            if ($probe.StatusCode -eq 200) { $live = $true; break }
        } catch {}
        Start-Sleep -Milliseconds 250
    }
    if (-not $live) {
        $failedChildren = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($process.Id)" -ErrorAction SilentlyContinue | Where-Object {
            [string]$_.CommandLine -match "uvicorn" -and [string]$_.CommandLine -match "app:app" -and
            [string]$_.CommandLine -match ([regex]::Escape($root)) -and [string]$_.CommandLine -match ("(?:--port\s+|--port=)" + [regex]::Escape([string]$Port))
        })
        foreach ($failedChild in $failedChildren) { Stop-Process -Id ([int]$failedChild.ProcessId) -Force -ErrorAction SilentlyContinue }
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
        $errors.Add("PDV Odysseus liveness probe")
        Write-ReportAndExit 1
    }
    $processStarted = $true
    $processId = $process.Id
    Write-ReportAndExit 0
}

if (-not (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
    $errors.Add("PDV Odysseus PID file")
    Write-ReportAndExit 1
}
try { $pidReceipt = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json } catch { $pidReceipt = $null }
$pidValue = if ($pidReceipt) { [int]$pidReceipt.pid } else { 0 }
if (-not $pidReceipt -or $pidReceipt.schemaVersion -ne 1 -or $pidValue -le 0) {
    $errors.Add("valid PDV Odysseus PID")
    Write-ReportAndExit 1
}
$candidateProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $pidValue" -ErrorAction SilentlyContinue
if (-not $candidateProcess) {
    Remove-Item -LiteralPath $pidFile -Force
    $errors.Add("running PDV Odysseus process")
    Write-ReportAndExit 1
}
$expectedPython = [System.IO.Path]::GetFullPath($PythonExecutable)
$actualExecutable = [System.IO.Path]::GetFullPath([string]$candidateProcess.ExecutablePath)
$commandLine = [string]$candidateProcess.CommandLine
$receiptRoot = [System.IO.Path]::GetFullPath([string]$pidReceipt.repositoryRoot)
$receiptExecutable = [System.IO.Path]::GetFullPath([string]$pidReceipt.executable)
$appDirPattern = [regex]::Escape($root)
$portPattern = "(?:--port\s+|--port=)" + [regex]::Escape([string]$Port) + "(?:\s|" + '"' + "|$)"
if ($receiptRoot -ne $root -or [int]$pidReceipt.port -ne $Port -or $receiptExecutable -ne $expectedPython -or
    $actualExecutable -ne $expectedPython -or $commandLine -notmatch "uvicorn" -or $commandLine -notmatch "app:app" -or
    $commandLine -notmatch ("--app-dir\s+" + '"' + "?$appDirPattern") -or $commandLine -notmatch $portPattern) {
    $errors.Add("PID belongs to this checkout's Uvicorn process")
    Write-ReportAndExit 1
}
$childProcesses = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $pidValue" -ErrorAction SilentlyContinue)
$serverChildren = @($childProcesses | Where-Object { [string]$_.CommandLine -match "uvicorn" -and [string]$_.CommandLine -match "app:app" })
foreach ($childProcess in $serverChildren) {
    $childCommandLine = [string]$childProcess.CommandLine
    if ($childCommandLine -notmatch "uvicorn" -or $childCommandLine -notmatch "app:app" -or
        $childCommandLine -notmatch ("--app-dir\s+" + '"' + "?$appDirPattern") -or $childCommandLine -notmatch $portPattern) {
        $errors.Add("PID child belongs to this checkout's Uvicorn process")
        Write-ReportAndExit 1
    }
}
foreach ($childProcess in $serverChildren) {
    Stop-Process -Id ([int]$childProcess.ProcessId) -Force -ErrorAction Stop
}
Stop-Process -Id $pidValue -ErrorAction Stop
Remove-Item -LiteralPath $pidFile -Force
$processStopped = $true
$processId = $pidValue
Write-ReportAndExit 0
