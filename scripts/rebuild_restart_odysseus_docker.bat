@echo off
setlocal
title Rebuild and Restart Docker Odysseus

set "REPO=%~dp0.."
set "LOG=%REPO%\logs\odysseus-docker-rebuild.log"

cd /d "%REPO%" || (
    echo ERROR: Could not enter the Odysseus repository.
    exit /b 1
)

if not exist "%REPO%\logs" mkdir "%REPO%\logs"

echo.
echo ============================================================
echo Rebuild and restart Docker Odysseus
echo Repository: %REPO%
echo Log:        %LOG%
echo ============================================================
echo.

where docker >nul 2>&1 || (
    echo ERROR: Docker was not found. Start or install Docker Desktop.
    exit /b 1
)

docker info >nul 2>&1 || (
    echo ERROR: Docker Engine is unavailable. Start Docker Desktop and wait until it is ready.
    exit /b 1
)

echo [%date% %time%] Validating Docker Compose configuration...>>"%LOG%"
docker compose config --quiet >>"%LOG%" 2>&1
if errorlevel 1 (
    echo ERROR: Docker Compose validation failed.
    echo Nothing was rebuilt or restarted.
    echo Review: "%LOG%"
    exit /b 1
)

set "APP_BIND=127.0.0.1"
set "APP_PORT=7000"

echo Building the Odysseus image with visible progress...
echo The first build can take several minutes. Do not close this window while progress continues.
echo Supporting services and persistent data will not be removed.
echo [%date% %time%] Starting image build...>>"%LOG%"

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$enc = New-Object System.Text.UTF8Encoding($false); & cmd.exe /d /s /c 'docker compose --progress plain build odysseus 2>&1' | ForEach-Object { $line = $_.ToString(); Write-Host $line; [System.IO.File]::AppendAllText('%LOG%', $line + [Environment]::NewLine, $enc) }; exit $LASTEXITCODE"
if errorlevel 1 (
    echo ERROR: The Odysseus image build failed.
    echo The existing running container was not recreated.
    echo Review: "%LOG%"
    exit /b 1
)

echo.
echo Build succeeded. Recreating only the Odysseus container...
echo [%date% %time%] Recreating Odysseus service...>>"%LOG%"

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$enc = New-Object System.Text.UTF8Encoding($false); & cmd.exe /d /s /c 'docker compose up -d --no-deps --force-recreate odysseus 2>&1' | ForEach-Object { $line = $_.ToString(); Write-Host $line; [System.IO.File]::AppendAllText('%LOG%', $line + [Environment]::NewLine, $enc) }; exit $LASTEXITCODE"
if errorlevel 1 (
    echo ERROR: The image built successfully, but Odysseus recreation failed.
    echo Review: "%LOG%"
    exit /b 1
)

echo Waiting for Odysseus to become reachable...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ready=$false; for($i=1; $i -le 30; $i++){ try { $r=Invoke-WebRequest 'http://127.0.0.1:7000' -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop; if($r.StatusCode -ge 200 -and $r.StatusCode -lt 500){ Write-Host ('Odysseus is ready. HTTP status: ' + $r.StatusCode); $ready=$true; break } } catch {}; Write-Host ('Waiting... attempt ' + $i + ' of 30'); Start-Sleep -Seconds 2 }; if(-not $ready){ exit 1 }"
if errorlevel 1 (
    echo.
    echo ERROR: The container was recreated, but Odysseus did not become reachable within the allowed time.
    echo Recent logs:
    docker compose logs --tail 100 odysseus
    echo.
    echo Full rebuild log: "%LOG%"
    exit /b 1
)

echo.
docker compose ps odysseus
if errorlevel 1 (
    echo WARNING: Odysseus responded, but Docker Compose status could not be displayed.
)
echo.
echo SUCCESS: Docker Odysseus was rebuilt and restarted.
echo Open: http://127.0.0.1:7000
echo Log:  "%LOG%"
echo [%date% %time%] Rebuild completed successfully.>>"%LOG%"
echo.

endlocal
exit /b 0
