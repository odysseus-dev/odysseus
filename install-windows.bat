@echo off
title Odysseus Installer
echo Starting Odysseus Installer...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-service.ps1"
pause
