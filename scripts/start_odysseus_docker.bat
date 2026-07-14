@echo off
setlocal EnableExtensions

set "ODYSSEUS_DIR=C:\odysseus"
set "LOG_DIR=%ODYSSEUS_DIR%\logs"
set "LOG_FILE=%LOG_DIR%\odysseus-docker-startup.log"
set "APP_BIND=127.0.0.1"
set "APP_PORT=7000"

if not exist "%ODYSSEUS_DIR%\docker-compose.yml" (
    echo ERROR: Could not find "%ODYSSEUS_DIR%\docker-compose.yml".
    exit /b 1
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
cd /d "%ODYSSEUS_DIR%"

where docker >nul 2>&1
if errorlevel 1 (
    echo [%date% %time%] ERROR: Docker was not found in PATH.>>"%LOG_FILE%"
    echo ERROR: Docker was not found in PATH. Install or start Docker Desktop.
    exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
    echo [%date% %time%] ERROR: Docker Engine is unavailable.>>"%LOG_FILE%"
    echo ERROR: Docker Engine is unavailable. Start Docker Desktop and wait until it is ready.
    exit /b 1
)

echo [%date% %time%] Starting the standard Odysseus Docker stack on http://%APP_BIND%:%APP_PORT%>>"%LOG_FILE%"
docker compose up -d >>"%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo [%date% %time%] ERROR: docker compose up failed with exit code %EXIT_CODE%.>>"%LOG_FILE%"
    echo ERROR: Odysseus Docker startup failed with exit code %EXIT_CODE%.
    echo See "%LOG_FILE%" for details.
    exit /b %EXIT_CODE%
)

echo [%date% %time%] Docker Compose accepted the startup request.>>"%LOG_FILE%"
echo Odysseus Docker startup requested successfully.
echo Open http://%APP_BIND%:%APP_PORT% after the containers finish starting.
exit /b 0
