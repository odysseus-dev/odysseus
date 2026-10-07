@echo off
setlocal
REM Start Odysseus (installed by setup.bat into the "odysseus" folder next to this file)

set "APP=%~dp0odysseus"
set "PORT=7000"

if not exist "%APP%\venv\Scripts\python.exe" (
    echo Odysseus is not installed yet. Please run setup.bat first.
    pause
    exit /b 1
)

cd /d "%APP%"

echo Starting Odysseus on http://localhost:%PORT% ...
echo Press Ctrl+C in this window to stop it.

REM Open the browser a few seconds after the server starts
start "" cmd /c "timeout /t 8 /nobreak >nul & start http://localhost:%PORT%"

"venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port %PORT%

pause
