@echo off
REM update_windows.bat — DEPRECATED.
REM
REM Use powershell -File .\odysseus.ps1 --update --launch=docker instead.

echo update_windows.bat is deprecated - use powershell -File .\odysseus.ps1 --update --launch=docker
echo (forwarding; this shim will be removed in a future release)
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0odysseus.ps1" --update --launch=docker
