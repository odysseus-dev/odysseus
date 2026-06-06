#!/bin/bash
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

PORT="${ODYSSEUS_PORT:-${APP_PORT:-7000}}"
HOST="${ODYSSEUS_HOST:-${APP_BIND:-127.0.0.1}}"

trap 'echo; echo "Setup failed. Safe to re-run ./start-odysseus.sh"; exit 1' ERR

echo "▶ Odysseus quick start for Linux"

PY="$(command -v python3)"
if [ -z "$PY" ]; then
  echo "✗ Python 3 not found. Install Python 3.11+ (e.g. sudo apt install python3 python3-venv python3-pip)"
  exit 1
fi

echo "  Python: $("$PY" --version 2>&1)"

if [ ! -f .env ]; then
  echo "▶ Creating .env from .env.example"
  cp .env.example .env
fi

if [ ! -d venv ]; then
  echo "▶ Creating Python virtual environment"
  "$PY" -m venv venv
fi

VENV_PY="./venv/bin/python3"

REQ_HASH="$(md5sum requirements.txt 2>/dev/null | cut -d' ' -f1)"
REQ_HASH_FILE="venv/.requirements_hash"
if [ ! -f "$REQ_HASH_FILE" ] || [ "$REQ_HASH" != "$(cat "$REQ_HASH_FILE" 2>/dev/null)" ]; then
  echo "▶ Installing Python packages (first run takes a few minutes)"
  "$VENV_PY" -m pip install --quiet --upgrade pip
  "$VENV_PY" -m pip install -r requirements.txt
  echo "$REQ_HASH" > "$REQ_HASH_FILE"
else
  echo "▶ Python packages up to date"
fi

if "$VENV_PY" -m pip show chromadb-client >/dev/null 2>&1; then
  echo "▶ Cleaning up conflicting chromadb-client package"
  "$VENV_PY" -m pip uninstall -y chromadb-client
  "$VENV_PY" -m pip install --force-reinstall chromadb
fi

echo "▶ Running first-time setup"
ODYSSEUS_SKIP_RUN_HINT=1 "$VENV_PY" setup.py

PROBE_HOST="$HOST"
if [ "$PROBE_HOST" = "0.0.0.0" ] || [ "$PROBE_HOST" = "::" ]; then
  PROBE_HOST="127.0.0.1"
fi
URL="http://$PROBE_HOST:$PORT"

echo
echo "▶ Starting Odysseus at $URL"
echo "  (press Ctrl+C to stop)"
echo
"$VENV_PY" -m uvicorn app:app --host "$HOST" --port "$PORT"
