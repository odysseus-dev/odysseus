#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${ODYSSEUS_REPO_ROOT:-}"
if [[ -z "$ROOT" ]]; then
  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

HOST="${ODYSSEUS_HOST:-127.0.0.1}"
PORT="${ODYSSEUS_PORT:-7000}"
URL="http://${HOST}:${PORT}"
DATA_DIR="$ROOT/data"
LOG_DIR="$ROOT/logs"
PID_FILE="$DATA_DIR/odysseus.pid"
LOG_FILE="$LOG_DIR/macos-launcher.log"
PYTHON_BIN="$ROOT/venv/bin/python"

mkdir -p "$DATA_DIR" "$LOG_DIR"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG_FILE"
}

healthy() {
  /usr/bin/curl -fsS --max-time 2 "$URL/api/health" >/dev/null 2>&1
}

open_app() {
  /usr/bin/open "$URL" >/dev/null 2>&1 || true
}

open_installer() {
  if [[ -x "$ROOT/macos/install-macos.command" ]]; then
    /usr/bin/open -a Terminal "$ROOT/macos/install-macos.command" >/dev/null 2>&1 || true
  fi
}

if healthy; then
  log "Odysseus already running at $URL"
  open_app
  exit 0
fi

if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$OLD_PID" ]] && kill -0 "$OLD_PID" >/dev/null 2>&1; then
    log "Existing Odysseus process $OLD_PID is still starting. Opening $URL."
    open_app
    exit 0
  fi
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  log "Virtual environment is missing. Run macos/install-macos.command first."
  open_installer
  exit 1
fi

if ! "$PYTHON_BIN" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  log "Core Python dependencies are missing. Re-running the macOS installer."
  open_installer
  exit 1
fi

cd "$ROOT"

if [[ ! -f "$ROOT/.env" || ! -f "$DATA_DIR/app.db" || ! -f "$DATA_DIR/auth.json" ]]; then
  log "Running setup.py because local data/env files are incomplete."
  "$PYTHON_BIN" "$ROOT/setup.py" >> "$LOG_FILE" 2>&1 || true
fi

log "Starting Odysseus at $URL"
export PYTHONUNBUFFERED=1
nohup "$PYTHON_BIN" -m uvicorn app:app --host "$HOST" --port "$PORT" >> "$LOG_FILE" 2>&1 &
PID="$!"
echo "$PID" > "$PID_FILE"

for _ in $(seq 1 60); do
  if healthy; then
    log "Odysseus is ready."
    open_app
    exit 0
  fi
  if ! kill -0 "$PID" >/dev/null 2>&1; then
    log "Odysseus exited during startup. Opening log file."
    /usr/bin/open "$LOG_FILE" >/dev/null 2>&1 || true
    exit 1
  fi
  sleep 1
done

log "Timed out waiting for Odysseus to become healthy. Opening log file."
/usr/bin/open "$LOG_FILE" >/dev/null 2>&1 || true
exit 1
