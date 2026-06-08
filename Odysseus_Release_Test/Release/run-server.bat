@echo off

echo ===================================================

echo             Starting Odysseus Backend Server       

echo ===================================================

echo.



:: 1. Force the script to look at its current root folder location

cd /d "%~dp0"



:: 2. Step INSIDE the odysseus folder to execute everything

cd odysseus



:: 3. Build the environment automatically if it's missing inside the subfolder

if not exist "venv" (

    echo [INFO] Local Python environment missing. Initializing setup...

    python -m venv venv

    echo [INFO] Installing required project modules...

    .\venv\Scripts\pip install -r requirements.txt

)



:: 4. Launch the live Uvicorn deployment environment

echo [INFO] Launching API listener on port 7000...

.\venv\Scripts\python -m uvicorn app:app --host 127.0.0.1 --port 7000



pause