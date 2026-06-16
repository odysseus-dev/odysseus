@echo off
setlocal

set "_scriptDir=%~dp0"
set "_pwshExe="

where pwsh >nul 2>nul
if not errorlevel 1 set "_pwshExe=pwsh.exe"
if not defined _pwshExe set "_pwshExe=powershell.exe"

"%_pwshExe%" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%_scriptDir%odysseus.ps1" %*
exit /b %ERRORLEVEL%
