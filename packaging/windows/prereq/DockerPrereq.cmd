@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0DockerPrereq.ps1"
exit /b %ERRORLEVEL%
