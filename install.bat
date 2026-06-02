@echo off
echo ========================================================
echo Odysseus Windows Installer
echo ========================================================
echo.

echo [1/4] Checking Python installation...
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo ERROR: Python is not installed or not in PATH.
    echo Please install Python 3.11+ from python.org and check "Add to PATH".
    pause
    exit /b 1
)

echo [2/4] Setting up Python virtual environment and dependencies...
if not exist venv\Scripts\python.exe (
    python -m venv venv
)
call venv\Scripts\activate.bat
pip install --upgrade pip --quiet
pip install -r requirements.txt
python setup.py

echo [3/4] Installing Windows compiler tools...
pip install pyinstaller pywebview

echo [4/4] Compiling Odysseus.exe...
pyinstaller --noconsole --onefile --icon=docs\odysseus.ico --name Odysseus launcher.py

:: Move the generated executable to the main folder
move /y dist\Odysseus.exe . >nul
:: Clean up build artifacts
rmdir /s /q build >nul 2>&1
rmdir /s /q dist >nul 2>&1
del /q Odysseus.spec >nul 2>&1

echo.
echo ========================================================
echo INSTALLATION COMPLETE!
echo ========================================================
echo You can now double-click "Odysseus.exe" to start the app!
echo.
pause
