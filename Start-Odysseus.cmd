@echo off
setlocal
set "ODYSSEUS_ROOT=%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$listener = Get-NetTCPConnection -LocalPort 7000 -State Listen -ErrorAction SilentlyContinue; if (-not $listener) { Start-Process -FilePath '%ODYSSEUS_ROOT%venv\Scripts\python.exe' -ArgumentList '-m','uvicorn','app:app','--host','127.0.0.1','--port','7000' -WorkingDirectory '%ODYSSEUS_ROOT%' -WindowStyle Hidden }; Start-Process 'http://127.0.0.1:7000'"
