#!/bin/bash
# odysseus-app.sh — the bash worker that Odysseus.app drives.
#
# Responsibilities:
#   * Install path validation (the repo's INSTALL_DIR must exist; if not,
#     report the issue back to the Swift host and exit non-zero).
#   * First-run setup: create the venv, install requirements, run setup.py.
#   * data/ relocation: when ODYSSEUS_FROM_APP=1 is set, symlink ./data
#     to ~/Library/Application Support/Odysseus/data so uvicorn (which
#     uses "data/..." relative paths) finds user data in the canonical
#     macOS location. The symlink lives in the repo but the data lives
#     in Application Support — same trick npm, cargo, etc. use.
#   * uvicorn lifecycle: start, wait for readiness, wait for shutdown.
#   * Status reporting: write app-state.json so the Swift NSStatusItem
#     menu bar item can show what the worker is doing.
#
# The Swift host (OdysseusLauncher) spawns this script and forwards
# SIGTERM on Cmd-Q; we trap TERM/INT/EXIT and clean up the uvicorn
# child + the status file.

set -e

# ── Resolve INSTALL_DIR (baked in at build time) ─────────────────────────
INSTALL_DIR="__INSTALL_DIR__"
APP_SUPPORT_DIR="$HOME/Library/Application Support/Odysseus"
STATE_FILE="$APP_SUPPORT_DIR/state.json"
PORT="${ODYSSEUS_PORT:-7860}"
URL="http://127.0.0.1:${PORT}"
UVICORN="$INSTALL_DIR/venv/bin/uvicorn"
SERVER_PID=""

# INSTALL_DIR may itself be a symlink (e.g. when installed via
# Homebrew, the bin entry is a symlink into libexec). Follow it so
# the venv + uvicorn are found at the real path.
if [ -L "$INSTALL_DIR" ]; then
  INSTALL_DIR="$(cd -P "$(dirname "$INSTALL_DIR")" && pwd)/$(basename "$INSTALL_DIR")"
  # Re-resolve if the target is also a symlink (chained symlinks).
  while [ -L "$INSTALL_DIR" ]; do
    TARGET=$(readlink "$INSTALL_DIR")
    [[ "$TARGET" == /* ]] && INSTALL_DIR="$TARGET" || INSTALL_DIR="$(dirname "$INSTALL_DIR")/$TARGET"
  done
fi
UVICORN="$INSTALL_DIR/venv/bin/uvicorn"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

mkdir -p "$APP_SUPPORT_DIR"

# ── Status file helpers ──────────────────────────────────────────────────
# The Swift NSStatusItem watches this file. Format: {state, port, pid, url, message}
write_state() {
  local state="$1" msg="$2"
  python3 - "$STATE_FILE" "$state" "$PORT" "${SERVER_PID:-0}" "$URL" "$msg" <<'PY'
import json, os, sys
path, state, port, pid, url, msg = sys.argv[1:7]
try:
    pid = int(pid)
except (TypeError, ValueError):
    pid = 0
payload = {"state": state, "port": int(port), "pid": pid, "url": url, "message": msg}
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(payload, f)
os.replace(tmp, path)
PY
}

cleanup() {
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    # Give uvicorn a moment to flush + close connections.
    for _ in 1 2 3 4 5; do
      kill -0 "$SERVER_PID" 2>/dev/null || break
      sleep 1
    done
    kill -9 "$SERVER_PID" 2>/dev/null || true
  fi
  # Don't clobber an existing "error" state with "stopped" — the user
  # clicked the menu bar item to see *what* went wrong, and "stopped"
  # hides the diagnostic. We only write "stopped" if the last state
  # was running or starting.
  current=""
  if [ -f "$STATE_FILE" ]; then
    current=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('state',''))" "$STATE_FILE" 2>/dev/null || echo "")
  fi
  case "$current" in
    error) ;;  # preserve the error
    *)     write_state "stopped" "Server stopped." ;;
  esac
}
# Catch SIGHUP too: the Swift host dying (e.g. from a parent kill) sends
# SIGHUP to the bash worker. Without trapping it, cleanup() never runs
# and the state file stays "starting" forever.
trap cleanup EXIT TERM INT HUP

# ── Install-path validation ─────────────────────────────────────────────
# Two failure modes:
#   1. INSTALL_DIR doesn't exist at all (user moved the repo).
#   2. INSTALL_DIR exists but odysseus.sh is missing (corrupt / partial clone).
# In both cases we surface a clear message and exit, so the Swift host
# can show a dialog with a fix.
if [ ! -d "$INSTALL_DIR" ]; then
  write_state "error" "Install folder not found: $INSTALL_DIR"
  echo "Install folder not found: $INSTALL_DIR" >&2
  exit 1
fi
if [ ! -x "$INSTALL_DIR/odysseus.sh" ]; then
  write_state "error" "Repo at $INSTALL_DIR is missing odysseus.sh"
  echo "Repo at $INSTALL_DIR is missing odysseus.sh — was it partially moved?" >&2
  exit 1
fi

# ── data/ relocation for .app launches ──────────────────────────────────
# The Python app uses relative "data/..." paths everywhere. We satisfy
# that with a symlink: ./data -> ~/Library/Application Support/Odysseus/data.
# Terminal launches skip this — they want ./data in-place.
if [ "${ODYSSEUS_FROM_APP:-0}" = "1" ]; then
  cd "$INSTALL_DIR"
  if [ -e "data" ] && [ ! -L "data" ]; then
    # Repo has a real data/ directory. Move it into Application Support
    # so the user's existing data isn't lost on first .app launch.
    mkdir -p "$APP_SUPPORT_DIR"
    if [ ! -e "$APP_SUPPORT_DIR/data" ]; then
      mv "data" "$APP_SUPPORT_DIR/data"
    else
      # Both exist — keep the one in Application Support, drop the repo copy.
      rm -rf "data"
    fi
  fi
  mkdir -p "$APP_SUPPORT_DIR/data"
  ln -sfn "$APP_SUPPORT_DIR/data" "data"
  # logs go to Application Support too — terminal launches keep them in
  # the repo, .app launches put them in the standard place.
  if [ ! -e "logs" ]; then
    mkdir -p "$APP_SUPPORT_DIR/logs"
    ln -sfn "$APP_SUPPORT_DIR/logs" "logs"
  fi
fi

# ── First-run setup ─────────────────────────────────────────────────────
write_state "starting" "Checking dependencies…"
cd "$INSTALL_DIR"

if [ ! -x "$UVICORN" ]; then
  write_state "starting" "Installing Python dependencies (first run; one-time)…"
  # Reuse the launcher. --launch=native idempotently sets up the venv.
  ODYSSEUS_SKIP_RUN_HINT=1 ./odysseus.sh --launch=native --no-open --port="$PORT" --host=127.0.0.1 || true
fi

if [ ! -x "$UVICORN" ]; then
  write_state "error" "Could not set up the venv. Run 'odysseus --launch=native' in Terminal for details."
  exit 1
fi

# Already running? Just open the UI.
if /usr/bin/curl -s -o /dev/null --max-time 2 "$URL" 2>/dev/null; then
  write_state "running" "Already up at $URL"
  # Sleep until killed. The Swift host is in charge of the lifecycle.
  while true; do sleep 1; done
fi

# ── Start uvicorn ───────────────────────────────────────────────────────
write_state "starting" "Starting server…"
APP_LOG="$APP_SUPPORT_DIR/uvicorn.log"
if [ "$(uname -m)" = "arm64" ]; then
  arch -arm64 "$UVICORN" app:app --host 127.0.0.1 --port "$PORT" >>"$APP_LOG" 2>&1 &
else
  "$UVICORN" app:app --host 127.0.0.1 --port "$PORT" >>"$APP_LOG" 2>&1 &
fi
SERVER_PID=$!
write_state "starting" "Waiting for $URL (this can take ~2 min on first run)…"

# Wait for readiness, up to 120s. Cold start downloads an embedding model.
READY=0
for _ in $(seq 1 120); do
  if /usr/bin/curl -s -o /dev/null --max-time 2 "$URL" 2>/dev/null; then
    READY=1
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    write_state "error" "Server failed to start. Log: $APP_LOG"
    exit 1
  fi
  sleep 1
done

if [ "$READY" = "1" ]; then
  write_state "running" "Up at $URL"
else
  write_state "running" "Slow start (model download?) — open $URL once it's ready"
fi

# Block until the server exits (or the Swift host kills us). The trap on
# EXIT will call cleanup(), which SIGTERMs the server and waits.
wait "$SERVER_PID"
