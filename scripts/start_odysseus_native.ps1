# Start Odysseus natively on Windows without Docker.
# Brings up the two services the app needs:
#   1. ChromaDB vector store  (localhost:8100)  -- Memory search + RAG
#   2. Odysseus FastAPI app   (localhost:7000)
#
# The PyPI `chromadb` wheel installs as a "thin" client with
# `is_thin_client = True` in venv/.../chromadb/is_thin_client.py, which makes
# a local Chroma server boot but hang on every request. Flip it to False once
# (the [server] extra installs the rust bindings that make it work):
#   echo 'is_thin_client = False' > venv/Lib/site-packages/chromadb/is_thin_client.py
# Re-apply after any `pip install chromadb` / venv rebuild.

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $Root          # repo root
$VenvPy = Join-Path $Root 'venv\Scripts\python.exe'

function Start-Background {
    param($Name, $Args)
    Write-Host "Starting $Name ..."
    $p = Start-Process -FilePath $VenvPy -ArgumentList $Args -WorkingDirectory $Root -WindowStyle Hidden -PassThru
    return $p
}

# 1. Chroma (needed by Memory/RAG). Skip if already listening.
$chromaUp = (Get-NetTCPConnection -LocalPort 8100 -State Listen -ErrorAction SilentlyContinue)
if (-not $chromaUp) {
    Start-Background 'ChromaDB' @('scripts/run_chromadb.py')
    Start-Sleep -Seconds 8
} else {
    Write-Host 'ChromaDB already listening on :8100'
}

# 2. Odysseus app
$appUp = (Get-NetTCPConnection -LocalPort 7000 -State Listen -ErrorAction SilentlyContinue)
if (-not $appUp) {
    Start-Background 'Odysseus' @('-m','uvicorn','app:app','--host','127.0.0.1','--port','7000')
} else {
    Write-Host 'Odysseus already listening on :7000'
}

Start-Sleep -Seconds 4
Write-Host ''
Write-Host 'Odysseus:  http://127.0.0.1:7000'
Write-Host 'ChromaDB:  http://127.0.0.1:8100'
