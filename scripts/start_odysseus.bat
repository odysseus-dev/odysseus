@echo off
setlocal

set "ODYSSEUS_DIR=C:\odysseus"
set "HOST=127.0.0.1"
set "PORT=7000"
set "LOG_DIR=%ODYSSEUS_DIR%\logs"
set "LOG_FILE=%LOG_DIR%\odysseus-startup.log"

if not exist "%ODYSSEUS_DIR%\app.py" (
    echo [%date% %time%] ERROR: Could not find "%ODYSSEUS_DIR%\app.py".
    echo Check that Odysseus is installed in C:\odysseus.
    pause
    exit /b 1
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

cd /d "%ODYSSEUS_DIR%"

where python >nul 2>&1
if errorlevel 1 (
    echo [%date% %time%] ERROR: Python was not found in PATH.>>"%LOG_FILE%"
    echo Python was not found in PATH.
    pause
    exit /b 1
)

echo [%date% %time%] Starting Odysseus on http://%HOST%:%PORT%>>"%LOG_FILE%"

python -m uvicorn app:app --host %HOST% --port %PORT% >>"%LOG_FILE%" 2>&1

set "EXIT_CODE=%ERRORLEVEL%"
echo [%date% %time%] Odysseus exited with code %EXIT_CODE%.>>"%LOG_FILE%"

if not "%EXIT_CODE%"=="0" (
    echo Odysseus stopped with exit code %EXIT_CODE%.
    echo See "%LOG_FILE%" for details.
    pause
)

exit /b %EXIT_CODE%
