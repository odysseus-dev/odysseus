@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Odysseus.ps1" %*
exit /b %ERRORLEVEL%
