#Requires -Version 5.1

#Windows Shutdown
  


$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$pidFile = ".\odysseus.pid"

if (Test-Path $pidFile) {
    $processId = Get-Content $pidFile
    Write-Host "`n==> Found background server running (PID: $processId)" -ForegroundColor Cyan
    
    try {
        Stop-Process -Id $processId -Force
        Write-Host "==> Successfully terminated Odysseus." -ForegroundColor Green
        Remove-Item $pidFile -Force
    } catch {
        Write-Host "==> Could not stop process $processId. It might have already closed." -ForegroundColor Yellow
        Remove-Item $pidFile -Force
    }
    Write-Host ""
} else {
    Write-Host "`n==> No odysseus.pid file found. Are you sure the background server is running?`n" -ForegroundColor Yellow
}