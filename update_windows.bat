@echo off
setlocal

pushd "%~dp0" >nul

echo [DEPRECATED] update_windows.bat is a deprecated means to update the docker path/route.
echo              Use: odysseus update --launch docker
echo.

if exist "%~dp0odysseus.cmd" (
  call "%~dp0odysseus.cmd" update -Launch docker %*
) else (
  powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0odysseus.ps1" update -Launch docker %*
)

set "_rc=%ERRORLEVEL%"
popd >nul
exit /b %_rc%
