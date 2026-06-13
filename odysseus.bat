@echo off
title Odysseus
cd /d "%~dp0"

powershell -ExecutionPolicy Bypass -File "%~dp0launch-windows.ps1"
set EXIT_CODE=%ERRORLEVEL%
if %EXIT_CODE% NEQ 0 (
    echo.
    echo Odysseus exited with an error (code %EXIT_CODE%^).
    pause
)
exit /b %EXIT_CODE%