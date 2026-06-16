#Requires -Version 5.1

Write-Host ""
Write-Host "[DEPRECATED] build-windows-portable.ps1 is a deprecated way to build the standalone launcher." -ForegroundColor Yellow
Write-Host "             Use: .\odysseus run --launch standalone --force-build" -ForegroundColor Yellow
Write-Host ""

$script = Join-Path $PSScriptRoot "odysseus.ps1"
& powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $script run -Launch standalone -ForceBuild @args
exit $LASTEXITCODE
