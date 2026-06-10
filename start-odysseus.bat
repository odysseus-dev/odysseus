@echo off
cd /d "%~dp0"
echo Starting Odysseus...
echo.
echo Open http://localhost:7000 in your browser
echo Press Ctrl+C to stop the server
echo.
venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 7000
pause
