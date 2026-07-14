@echo off
setlocal EnableExtensions

set "ODYSSEUS_DIR=C:\odysseus"
set "LOG_DIR=%ODYSSEUS_DIR%\logs"
set "LOG_FILE=%LOG_DIR%\odysseus-docker-stop.log"
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
    echo ERROR: Docker was not found in PATH.
    exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
    echo [%date% %time%] ERROR: Docker Engine is unavailable.>>"%LOG_FILE%"
    echo ERROR: Docker Engine is unavailable. Start Docker Desktop before stopping the stack.
    exit /b 1
)

echo [%date% %time%] Stopping the standard Odysseus Docker stack.>>"%LOG_FILE%"
docker compose stop >>"%LOG_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo [%date% %time%] ERROR: docker compose stop failed with exit code %EXIT_CODE%.>>"%LOG_FILE%"
    echo ERROR: Odysseus Docker stop failed with exit code %EXIT_CODE%.
    echo See "%LOG_FILE%" for details.
    exit /b %EXIT_CODE%
)

echo [%date% %time%] The standard Odysseus Docker stack stopped.>>"%LOG_FILE%"
echo The standard Odysseus Docker stack stopped. Containers and persistent data were kept.
exit /b 0
