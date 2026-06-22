@echo off
setlocal

set "ROOT=%~dp0"
set "SCRIPT=%ROOT%scripts\android-pc-tools.ps1"

if not exist "%SCRIPT%" (
  echo Missing helper script: %SCRIPT%
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -Port 7000 -SetPcMode -LaunchApp
set "STATUS=%ERRORLEVEL%"

if not "%STATUS%"=="0" (
  echo.
  echo Android PC connection setup failed with exit code %STATUS%.
  echo Make sure Wireless debugging is on and the phone appears in: adb devices -l
  echo.
  if not "%ODYSSEUS_NO_PAUSE%"=="1" pause
)

exit /b %STATUS%
