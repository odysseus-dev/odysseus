@echo off
setlocal enabledelayedexpansion
title Start Odysseus

REM One command to start the whole project on demand (no auto-start at boot):
REM   - launches Docker Desktop if the engine is off, waits until it is ready
REM   - runs `docker compose up -d` (brings up Odysseus AND Ollama via the
REM     COMPOSE_FILE overlay set in .env)
REM   - opens the web UI
REM Run it from anywhere; it cd's into the project itself.
REM Stop everything later with:  docker compose stop

set "PROJECT_DIR=C:\Users\saimo\OneDrive\Desktop\projects\odysseus"
set "DOCKER_EXE=C:\Program Files\Docker\Docker\Docker Desktop.exe"

echo Starting Odysseus + Ollama...

docker info >nul 2>&1
if not errorlevel 1 goto compose

echo Docker not running. Launching Docker Desktop...
if not exist "%DOCKER_EXE%" ( echo ERROR: Docker Desktop not found at "%DOCKER_EXE%". Edit DOCKER_EXE in this script. & pause & exit /b 1 )
start "" "%DOCKER_EXE%"

echo Waiting for Docker engine...
set /a tries=0
:waitloop
timeout /t 3 >nul
docker info >nul 2>&1
if not errorlevel 1 goto compose
set /a tries+=1
if !tries! geq 40 ( echo ERROR: Docker did not become ready in time. & pause & exit /b 1 )
goto waitloop

:compose
cd /d "%PROJECT_DIR%"
docker compose up -d
if errorlevel 1 ( echo ERROR: docker compose up failed. See message above. & pause & exit /b 1 )
start "" http://localhost:7000
echo.
echo Done. Odysseus + Ollama are running at http://localhost:7000
echo Stop them later with:  docker compose stop
pause
