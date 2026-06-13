@echo off
title Odysseus
cd /d "%~dp0"

powershell -ExecutionPolicy Bypass -File "%~dp0launch-windows.ps1"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Odysseus exited with an error (code %ERRORLEVEL%^).
    pause
)