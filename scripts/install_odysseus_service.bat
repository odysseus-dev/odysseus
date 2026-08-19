@echo off
REM ===========================================================================
REM Install Odysseus as real Windows services (auto-start at boot).
REM RUN THIS AS ADMINISTRATOR (right-click -> Run as administrator).
REM
REM Creates two services:
REM   OdysseusChroma  - ChromaDB vector store (:8100)  [no deps]
REM   Odysseus        - FastAPI app         (:7000)  [depends on Chroma]
REM
REM NSSM is at C:\Users\Ore\nssm\nssm.exe
REM ===========================================================================

SETLOCAL
SET "NSSM=C:\Users\Ore\nssm\nssm.exe"
SET "ROOT=C:\Users\Ore\odysseus"
SET "PY=%ROOT%\venv\Scripts\python.exe"

IF NOT EXIST "%NSSM%" ( echo NSSM missing & exit /b 1 )
IF NOT EXIST "%PY%"   ( echo venv missing & exit /b 1 )

REM ---- 1. Chroma service --------------------------------------------------
"%NSSM%" stop OdysseusChroma >nul 2>&1
"%NSSM%" remove OdysseusChroma confirm >nul 2>&1
"%NSSM%" install OdysseusChroma "%PY%" "scripts/run_chromadb.py"
"%NSSM%" set OdysseusChroma AppDirectory "%ROOT%"
"%NSSM%" set OdysseusChroma DisplayName "Odysseus - ChromaDB Vector Store"
"%NSSM%" set OdysseusChroma Description "ChromaDB vector store for Odysseus Memory/RAG (port 8100)."
"%NSSM%" set OdysseusChroma Start SERVICE_AUTO_START
"%NSSM%" set OdysseusChroma AppExit Default Restart
"%NSSM%" set OdysseusChroma AppStdout "%ROOT%\logs\chroma_service.log"
"%NSSM%" set OdysseusChroma AppStderr "%ROOT%\logs\chroma_service.log"

REM ---- 2. Odysseus app service (depends on Chroma) ------------------------
"%NSSM%" stop Odysseus >nul 2>&1
"%NSSM%" remove Odysseus confirm >nul 2>&1
"%NSSM%" install Odysseus "%PY%" "-m uvicorn app:app --host 127.0.0.1 --port 7000"
"%NSSM%" set Odysseus AppDirectory "%ROOT%"
"%NSSM%" set Odysseus DisplayName "Odysseus - AI Workspace"
"%NSSM%" set Odysseus Description "Odysseus self-hosted AI workspace (port 7000)."
"%NSSM%" set Odysseus Start SERVICE_AUTO_START
"%NSSM%" set Odysseus DependOnService OdysseusChroma
"%NSSM%" set Odysseus AppExit Default Restart
"%NSSM%" set Odysseus AppStdout "%ROOT%\logs\odysseus_service.log"
"%NSSM%" set Odysseus AppStderr "%ROOT%\logs\odysseus_service.log"

echo.
echo Services installed. Starting them now...
"%NSSM%" start OdysseusChroma
timeout /t 6 >nul
"%NSSM%" start Odysseus

echo.
echo Done. Check status with:  sc query Odysseus
echo Logs: %ROOT%\logs\chroma_service.log  and  odysseus_service.log
ENDLOCAL
