@echo off
setlocal
title Odysseus Launcher

echo =========================================
echo Odysseus Native Windows Launcher
echo =========================================
echo.

powershell -ExecutionPolicy Bypass -File "%~dp0launch-windows.ps1" %*

if %ERRORLEVEL% neq 0 (
    echo.
    echo Launcher exited with an error.
    pause
)
