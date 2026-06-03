@echo off
title Odysseus AI Workspace

echo ========================================
echo    Starting Odysseus...
echo ========================================

:: Go to the folder where run.bat is located
cd /d "%~dp0"

:: Activate virtual environment using .bat (more reliable)
call venv\Scripts\activate.bat

if errorlevel 1 (
    echo.
    echo [ERROR] Virtual environment not found!
    echo Please run these commands once:
    echo python -m venv venv
    echo pip install -r requirements.txt
    pause
    exit /b 1
)

echo [OK] Virtual environment activated.

:: Install / fix missing packages
echo Installing dependencies...
pip install -r requirements.txt --quiet

echo Running setup...
python setup.py

echo.
echo ========================================
echo    Odysseus is starting...
echo    Please wait...
echo ========================================

:: Start the server
python -m uvicorn app:app --host 127.0.0.1 --port 7000

pause