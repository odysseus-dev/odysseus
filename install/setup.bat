@echo off
setlocal EnableExtensions
REM =============================================
REM Odysseus Local Self-Hosted Setup with Logging
REM Requires: Git for Windows + Python 3.11 or newer
REM Source:   https://github.com/odysseus-dev/odysseus
REM =============================================

REM ---- Settings you may want to change ----
set "PROJECT=odysseus"
set "REPO_URL=https://github.com/odysseus-dev/odysseus.git"
REM Leave BRANCH empty for the default branch (dev = newest, may be unstable).
REM Set BRANCH=main for the more curated, stable branch.
set "BRANCH="
REM Set to 1 to also install optional extras (local STT, DuckDuckGo search, PDF viewer, Office extraction).
set "INSTALL_OPTIONAL=0"

set "ROOT=%~dp0"
set "APP=%ROOT%%PROJECT%"
set "LOGFILE=%ROOT%odysseus_setup.log"

REM Set up logging
echo [%date% %time%] Starting Odysseus setup... > "%LOGFILE%"
echo Setup started at %date% %time%

REM 1) Check prerequisites
call :log "Checking prerequisites..."

where git >nul 2>&1
if errorlevel 1 goto :no_git

set "PYCMD="
for %%V in (3.12 3.11 3.13) do (
    if not defined PYCMD (
        py -%%V --version >nul 2>&1
        if not errorlevel 1 set "PYCMD=py -%%V"
    )
)
if not defined PYCMD (
    python -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,11) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYCMD=python"
)
if not defined PYCMD goto :no_python
call :log "Using Python launcher: %PYCMD%"

REM 2) Clone (or update) the repository
if exist "%APP%\.git" goto :pull_repo

call :log "Cloning Odysseus into %APP% ..."
if defined BRANCH (
    git clone --branch %BRANCH% "%REPO_URL%" "%APP%" >> "%LOGFILE%" 2>&1
) else (
    git clone "%REPO_URL%" "%APP%" >> "%LOGFILE%" 2>&1
)
if errorlevel 1 goto :clone_failed
goto :after_clone

:pull_repo
call :log "Existing clone found - pulling latest changes..."
git -C "%APP%" pull >> "%LOGFILE%" 2>&1
if errorlevel 1 call :log "WARNING: git pull failed - continuing with the existing files."

:after_clone
cd /d "%APP%"
call :log "Working folder: %cd%"

REM 3) Create .env from the example (optional but recommended)
if not exist ".env" if exist ".env.example" (
    copy ".env.example" ".env" >nul
    call :log "Created .env from .env.example."
)

REM 4) Create the virtual environment
if exist "venv\Scripts\python.exe" goto :venv_ready
call :log "Creating virtual environment..."
%PYCMD% -m venv venv >> "%LOGFILE%" 2>&1
if errorlevel 1 goto :venv_failed

:venv_ready
set "VPY=%APP%\venv\Scripts\python.exe"

REM 5) Install Python dependencies
call :log "Installing Python dependencies - this can take several minutes..."
"%VPY%" -m pip install --upgrade pip >> "%LOGFILE%" 2>&1
"%VPY%" -m pip install -r requirements.txt >> "%LOGFILE%" 2>&1
if errorlevel 1 goto :pip_failed

if "%INSTALL_OPTIONAL%"=="1" (
    call :log "Installing optional dependencies..."
    "%VPY%" -m pip install -r requirements-optional.txt >> "%LOGFILE%" 2>&1
    if errorlevel 1 call :log "WARNING: some optional dependencies failed to install."
)
call :log "Dependencies installed successfully."

REM 6) Run Odysseus first-time setup (creates the admin account)
REM    Output is shown on screen AND appended to the log.
call :log "Running first-time setup (setup.py)..."
echo.
echo ---- Watch for the temporary ADMIN PASSWORD printed below ----
set "PYTHONUNBUFFERED=1"
powershell -NoProfile -Command "& '%VPY%' setup.py 2>&1 | ForEach-Object { Write-Host $_; Add-Content -LiteralPath '%LOGFILE%' -Value $_ }; exit $LASTEXITCODE"
if errorlevel 1 goto :setup_failed
echo ---------------------------------------------------------------
call :log "First-time setup finished."

REM 7) Make sure start.bat is next to this script
if not exist "%ROOT%start.bat" (
    call :log "WARNING: start.bat not found next to setup.bat - copy it here to launch Odysseus."
    goto :done
)

REM 8) Final message
:done
echo.
echo =========================================
echo Odysseus setup complete!
echo -----------------------------------------
echo IMPORTANT:
echo - Odysseus will start on http://localhost:7000 by default.
echo - Log in as "admin" using the temporary password printed above,
echo   then change it under Settings. The password is also in the log file.
echo - Configure models, search and email inside Settings in the app.
echo - Optional: install Ollama and add http://localhost:11434/v1 as an endpoint.
echo - Keep AUTH_ENABLED=true in .env if you ever expose it beyond localhost.
echo - To stop: Ctrl+C in the server window.
echo - Log file saved to: %LOGFILE%
echo =========================================
call :log "Setup completed successfully."

if not exist "%ROOT%start.bat" goto :end
echo Launching Odysseus in 5 seconds...
timeout /t 5 /nobreak >nul
start "" cmd /k "%ROOT%start.bat"
goto :end

REM ---------------- Error handlers ----------------
:no_git
call :log "ERROR: Git was not found."
echo.
echo -------------------------------------------------------------------------------------------------------
echo ERROR: Git is not installed or not on PATH.
echo Download Git for Windows from https://git-scm.com/download/win and run this script again.
echo -------------------------------------------------------------------------------------------------------
pause
goto :end

:no_python
call :log "ERROR: Python 3.11 or newer was not found."
echo.
echo -------------------------------------------------------------------------------------------------------
echo ERROR: Python 3.11 or newer was not found.
echo Install Python 3.12 from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
echo or make sure the "py" launcher is installed, then run this script again.
echo -------------------------------------------------------------------------------------------------------
pause
goto :end

:clone_failed
call :log "ERROR: git clone failed."
echo.
echo ERROR: Could not clone the repository. Check your internet connection.
echo See the log file: %LOGFILE%
pause
goto :end

:venv_failed
call :log "ERROR: could not create the virtual environment."
echo.
echo ERROR: Could not create the Python virtual environment.
echo See the log file: %LOGFILE%
pause
goto :end

:pip_failed
call :log "ERROR: pip install failed."
echo.
echo -------------------------------------------------------------------------------------------------------
echo ERROR: Installing Python dependencies failed.
echo Check the log file: %LOGFILE% for details.
echo -------------------------------------------------------------------------------------------------------
pause
goto :end

:setup_failed
call :log "ERROR: setup.py failed."
echo.
echo ERROR: Odysseus first-time setup failed. See the log file: %LOGFILE%
pause
goto :end

:end
call :log "Setup script ended."
echo.
echo Setup finished. Check %LOGFILE% for the full log if issues occurred.
pause
endlocal
exit /b 0

REM ---------------- Helper: log to file and screen ----------------
:log
echo [%date% %time%] %~1 >> "%LOGFILE%"
echo %~1
exit /b 0
