$ErrorActionPreference = "Stop"
Set-Location -Path (Join-Path $PSScriptRoot "..")

$connections = Get-NetTCPConnection -LocalPort 7000 -ErrorAction SilentlyContinue
foreach ($connection in $connections) {
    Stop-Process -Id $connection.OwningProcess -Force -ErrorAction SilentlyContinue
}

$env:DATABASE_URL = ""
$env:ODYSSEUS_OPEN_BROWSER = "1"
& (Join-Path $PWD "venv\Scripts\python.exe") (Join-Path $PWD "launcher.py")