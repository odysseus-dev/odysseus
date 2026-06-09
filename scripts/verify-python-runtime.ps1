param(
    [string]$RuntimeDir = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
if (-not $RuntimeDir) {
    $RuntimeDir = Join-Path $repoRoot "python-runtime"
}

$pythonExe = Join-Path $RuntimeDir "python.exe"
$backendScript = Join-Path $repoRoot "python\rpc_server.py"
$backendPackage = Join-Path $repoRoot "python\odysseus_desktop_backend"
$icon = Join-Path $repoRoot "src-tauri\icons\icon.ico"
$license = Join-Path $repoRoot "LICENSE"
$notices = Join-Path $repoRoot "THIRD_PARTY_NOTICES.md"

foreach ($path in @($pythonExe, $backendScript, $backendPackage, $icon, $license, $notices)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required packaging path is missing: $path"
    }
}

$backendPath = Join-Path $repoRoot "python"
$verify = @"
import importlib
import os
import sqlite3
import sys
sys.path.insert(0, os.environ["ODYSSEUS_BACKEND_PATH"])
for module in ("json", "sqlite3", "numpy", "pypdf", "rpc_server", "odysseus_desktop_backend"):
    importlib.import_module(module)
print("runtime-ok")
"@

$oldPythonPath = $env:PYTHONPATH
$oldBackendPath = $env:ODYSSEUS_BACKEND_PATH
$verifyPath = Join-Path $env:TEMP "odysseus-verify-python-runtime.py"
try {
    $env:PYTHONPATH = $backendPath
    $env:ODYSSEUS_BACKEND_PATH = $backendPath
    Set-Content -LiteralPath $verifyPath -Value $verify -Encoding UTF8
    & $pythonExe $verifyPath
    if ($LASTEXITCODE -ne 0) {
        throw "Python runtime dependency verification failed."
    }
} finally {
    $env:PYTHONPATH = $oldPythonPath
    $env:ODYSSEUS_BACKEND_PATH = $oldBackendPath
    Remove-Item -LiteralPath $verifyPath -Force -ErrorAction SilentlyContinue
}

Write-Host "Python runtime and Tauri resource inputs verified."

$profileDir = Join-Path $env:TEMP ("odysseus-runtime-verify-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
try {
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $pythonExe
    $psi.WorkingDirectory = $repoRoot
    $psi.Arguments = ('-u "{0}"' -f $backendScript)
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $psi.Environment["ODYSSEUS_PROFILE_DIR"] = $profileDir

    $proc = [System.Diagnostics.Process]::Start($psi)
    $proc.StandardInput.WriteLine('{"jsonrpc":"2.0","id":1,"method":"health.ping","params":{}}')
    $proc.StandardInput.WriteLine('{"jsonrpc":"2.0","id":2,"method":"app.shutdown","params":{}}')
    $proc.StandardInput.Close()
    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit(10000) | Out-Null

    if (-not $proc.HasExited) {
        $proc.Kill()
        throw "Embedded Python sidecar did not exit after app.shutdown."
    }
    if ($proc.ExitCode -ne 0) {
        throw "Embedded Python sidecar exited with $($proc.ExitCode): $stderr"
    }
    if ($stdout -notmatch '"ok":true') {
        throw "Embedded Python sidecar health check failed: $stdout $stderr"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $profileDir "logs\backend.log"))) {
        throw "Embedded Python sidecar did not create profile backend.log."
    }
    Write-Host "Embedded Python JSON-RPC sidecar verified."
} finally {
    Remove-Item -LiteralPath $profileDir -Recurse -Force -ErrorAction SilentlyContinue
}
