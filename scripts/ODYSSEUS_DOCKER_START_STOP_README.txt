ODYSSEUS DOCKER START/STOP (WINDOWS)

REQUIREMENT
Docker Desktop must be installed, running, and ready before these scripts are used.
The scripts expect the repository at C:\odysseus and bind Odysseus to
http://127.0.0.1:7000.

START WITH A VISIBLE WINDOW
Double-click:
  scripts\start_odysseus_docker.bat

START HIDDEN
Double-click:
  scripts\start_odysseus_docker_hidden.vbs

The hidden launcher starts the same batch script without showing a console.
Startup details are appended to:
  logs\odysseus-docker-startup.log

STOP
Double-click:
  scripts\stop_odysseus_docker.bat

This runs "docker compose stop" for the standard stack. It does not run
"docker compose down", delete containers, volumes, images, or application data.
Stop details are appended to:
  logs\odysseus-docker-stop.log

STATUS AND LOGS (POWERSHELL)
  Set-Location C:\odysseus
  docker compose ps
  docker compose logs --tail 100 odysseus
  docker compose logs --tail 100 searxng chromadb ntfy

PERSISTENCE
Application data and logs remain under C:\odysseus\data and C:\odysseus\logs.
SearXNG, ChromaDB, ntfy, and the disposable workbench use Docker named
volumes. The stop script preserves all of them.

REBUILD AFTER SOURCE CHANGES
Changes made in C:\odysseus (mounted as /project/workspace) do not change the
running application snapshot at /app. After reviewing and testing changes, rebuild and
recreate Odysseus from Windows PowerShell:
  Set-Location C:\odysseus
  $env:APP_BIND = "127.0.0.1"
  $env:APP_PORT = "7000"
  docker compose up -d --build --no-deps odysseus
  Remove-Item Env:APP_BIND -ErrorAction SilentlyContinue
  Remove-Item Env:APP_PORT -ErrorAction SilentlyContinue

WORKSPACE AND DISPOSABLE WORKBENCH
Select /project as the single active Odysseus workspace. The real Windows
checkout is mounted read/write at /project/workspace, while the running app
stays at /app. A Docker named volume is mounted at /project/workbench for
disposable sandbox-first patch work. Do not copy .env, data, logs, credentials,
model caches, or other secrets into the workbench. Test candidate changes
there, then apply the reviewed patch to /project/workspace and test again before
rebuilding.

ROLLBACK TO THE NATIVE WINDOWS LAUNCHER
The original native scripts are preserved:
  scripts\start_odysseus.bat
  scripts\start_odysseus_hidden.vbs
  scripts\stop_odysseus.bat

Before starting native Odysseus, stop the Docker stack and confirm port 7000 is
free. Do not run the native and Docker versions on the same host port.

SAFETY
The standard Compose configuration does not mount the Docker socket. Do not add
the host-Docker overlay merely to use these scripts. Windows Firewall changes
are outside these scripts.
