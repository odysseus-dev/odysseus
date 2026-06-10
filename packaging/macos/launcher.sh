#!/usr/bin/env bash
# Odysseus.app launcher — Contents/MacOS/Odysseus
#
# Tokens __ODYSSEUS_PORT__ and __SEARXNG_PORT__ are replaced by build.sh.
#
# What this script does:
#   1. Resolves all paths relative to the .app bundle (fully portable)
#   2. Single-instance guard — exits if already running
#   3. On first launch: runs bootstrap.py to create data dir, DB, admin user
#   4. Starts SearXNG subprocess on __SEARXNG_PORT__
#   5. Starts Odysseus (PyInstaller frozen binary) on __ODYSSEUS_PORT__
#   6. run.py opens a native PyWebView window on the main thread
#   7. Waits for Odysseus to exit, then cleans up SearXNG

set -uo pipefail

ODYSSEUS_PORT="__ODYSSEUS_PORT__"
SEARXNG_PORT="__SEARXNG_PORT__"
APP_NAME="__APP_NAME__"

# ── Path resolution ────────────────────────────────────────────────────────────
MACOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTENTS_DIR="$(dirname "$MACOS_DIR")"
RES="$CONTENTS_DIR/Resources"

ODYSSEUS_BIN="$RES/odysseus_app/odysseus_app"
SEARXNG_PYTHON="$RES/searxng_runtime/python/bin/python3.11"
SEARXNG_VENV="$RES/searxng_runtime/venv"
SEARXNG_CONFIG_TEMPLATE="$RES/config/searxng/settings.yml"
BOOTSTRAP="$RES/bootstrap.py"
ENV_EXAMPLE="$RES/.env.example"

DATA_DIR="$HOME/Library/Application Support/Odysseus"
LOG_DIR="$DATA_DIR/logs"
SEARXNG_CONFIG_DIR="$DATA_DIR/searxng"
# Per-user lock in data dir (not /tmp which is shared across users)
LOCK_DIR="$DATA_DIR/.launcher.lock"

ODYSSEUS_URL="http://127.0.0.1:${ODYSSEUS_PORT}"
SEARXNG_URL="http://127.0.0.1:${SEARXNG_PORT}"

SEARXNG_PID=""
ODYSSEUS_PID=""
GUI_PID=""

# ── Utilities ─────────────────────────────────────────────────────────────────
notify() {
  /usr/bin/osascript \
    -e "display notification \"$1\" with title \"Odysseus\"" \
    >/dev/null 2>&1 || true
}

die_gui() {
  /usr/bin/osascript \
    -e "display dialog \"$1\" with title \"Odysseus\" buttons {\"OK\"} default button 1 with icon stop" \
    >/dev/null 2>&1 || true
  exit 1
}

# Ensure log dir exists before we try to write to it
mkdir -p "$LOG_DIR" 2>/dev/null || true
log() { echo "[odysseus] $*" >> "$LOG_DIR/launcher.log" 2>/dev/null || true; }

# ── Graceful cleanup ──────────────────────────────────────────────────────────
cleanup() {
  log "Shutting down..."
  # Graceful SIGTERM first
  [[ -n "$GUI_PID"      ]] && kill -TERM "$GUI_PID"      2>/dev/null || true
  [[ -n "$ODYSSEUS_PID" ]] && kill -TERM "$ODYSSEUS_PID" 2>/dev/null || true
  [[ -n "$SEARXNG_PID"  ]] && kill -TERM "$SEARXNG_PID"  2>/dev/null || true
  # Wait up to 5 seconds for graceful exit
  local deadline=$((SECONDS + 5))
  while [[ $SECONDS -lt $deadline ]]; do
    local any_running=0
    [[ -n "$GUI_PID"      ]] && kill -0 "$GUI_PID"      2>/dev/null && any_running=1
    [[ -n "$ODYSSEUS_PID" ]] && kill -0 "$ODYSSEUS_PID" 2>/dev/null && any_running=1
    [[ -n "$SEARXNG_PID"  ]] && kill -0 "$SEARXNG_PID"  2>/dev/null && any_running=1
    [[ $any_running -eq 0 ]] && break
    sleep 1
  done
  # Force kill anything still alive
  [[ -n "$GUI_PID"      ]] && kill -9 "$GUI_PID"      2>/dev/null || true
  [[ -n "$ODYSSEUS_PID" ]] && kill -9 "$ODYSSEUS_PID" 2>/dev/null || true
  [[ -n "$SEARXNG_PID"  ]] && kill -9 "$SEARXNG_PID"  2>/dev/null || true
  rm -f "$DATA_DIR/logs/.gui.pid" 2>/dev/null || true
  rm -rf "$LOCK_DIR" 2>/dev/null || true
  log "Shutdown complete"
}

# Bind cleanup to all termination signals (P0-2)
trap cleanup EXIT TERM INT HUP

# ── Single instance guard ─────────────────────────────────────────────────────
# Port check first — if already serving, notify and exit
if /usr/bin/lsof -i "TCP:${ODYSSEUS_PORT}" -sTCP:LISTEN -t >/dev/null 2>&1; then
  notify "Odysseus is already running"
  exit 0
fi

# Atomic mkdir lock — prevents race between two simultaneous launches
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  notify "Odysseus is already starting up"
  exit 0
fi

# ── Sanity checks ─────────────────────────────────────────────────────────────
[[ -x "$ODYSSEUS_BIN" ]] \
  || die_gui "Odysseus app bundle appears corrupted.\nMissing: $ODYSSEUS_BIN"
[[ -x "$SEARXNG_PYTHON" ]] \
  || die_gui "Odysseus app bundle appears corrupted.\nMissing: $SEARXNG_PYTHON"

# ── First-launch bootstrap ────────────────────────────────────────────────────
FIRST_LAUNCH_FLAG="$DATA_DIR/.bootstrapped"
if [[ ! -f "$FIRST_LAUNCH_FLAG" ]]; then
  notify "Setting up Odysseus for the first time..."
  BOOTSTRAP_EXIT=0
  BOOTSTRAP_OUT=$(
    ODYSSEUS_DATA_DIR="$DATA_DIR" \
    ODYSSEUS_ENV_EXAMPLE="$ENV_EXAMPLE" \
    ODYSSEUS_PORT="$ODYSSEUS_PORT" \
    SEARXNG_PORT="$SEARXNG_PORT" \
    PYTHONPATH="$RES/odysseus_app/_internal" \
    "$SEARXNG_PYTHON" \
      "$BOOTSTRAP" 2>&1
  ) || BOOTSTRAP_EXIT=$?

  # Write log but strip the password line (P1-4 security)
  echo "$BOOTSTRAP_OUT" | grep -v "Login:" >> "$LOG_DIR/bootstrap.log" 2>/dev/null || true
  log "Bootstrap completed with exit code $BOOTSTRAP_EXIT"

  # Show summary dialog via temp file (password shown here only — not in logs)
  _TMPF="$(mktemp "$DATA_DIR/.setup_XXXX.txt")"
  chmod 600 "$_TMPF"
  printf "%s" "$BOOTSTRAP_OUT" > "$_TMPF"
  /usr/bin/osascript \
    -e "set t to read POSIX file \"$_TMPF\"" \
    -e "display dialog t with title \"Odysseus - First Launch Setup\" buttons {\"OK\"} default button 1" \
    >/dev/null 2>&1 || true
  rm -f "$_TMPF"

  # Fail launch on bootstrap error (P1-3)
  if [[ $BOOTSTRAP_EXIT -ne 0 ]] || [[ ! -d "$DATA_DIR" ]]; then
    die_gui "First-launch setup failed.\nCheck: $LOG_DIR/bootstrap.log"
  fi

  touch "$FIRST_LAUNCH_FLAG"
fi

# ── Prepare SearXNG config ────────────────────────────────────────────────────
mkdir -p "$SEARXNG_CONFIG_DIR"
if [[ ! -f "$SEARXNG_CONFIG_DIR/settings.yml" ]]; then
  SEARXNG_SECRET="$(LC_ALL=C tr -dc 'a-f0-9' < /dev/urandom | head -c 64)"
  sed \
    -e "s|ultrasecretkey|${SEARXNG_SECRET}|g" \
    -e "s|SEARXNG_BASE_URL=.*|SEARXNG_BASE_URL=http://127.0.0.1:${SEARXNG_PORT}/|g" \
    "$SEARXNG_CONFIG_TEMPLATE" > "$SEARXNG_CONFIG_DIR/settings.yml"
fi

# ── Start SearXNG ─────────────────────────────────────────────────────────────
log "Starting SearXNG on port $SEARXNG_PORT..."
SEARXNG_SETTINGS_PATH="$SEARXNG_CONFIG_DIR/settings.yml" \
SEARXNG_PORT="$SEARXNG_PORT" \
"$SEARXNG_VENV/bin/python" \
  -m searx.webapp \
  --host "127.0.0.1" \
  --port "$SEARXNG_PORT" \
  >> "$LOG_DIR/searxng.log" 2>&1 &
SEARXNG_PID=$!
log "SearXNG PID: $SEARXNG_PID"

# ── Start Odysseus server (ODYSSEUS_MODE=server — headless, no GUI) ──────────
log "Starting Odysseus server on port $ODYSSEUS_PORT..."
ODYSSEUS_DATA="$DATA_DIR" \
DATABASE_URL="sqlite:///${DATA_DIR}/app.db" \
CHROMA_DB_PATH="${DATA_DIR}/chroma" \
SEARXNG_INSTANCE="http://127.0.0.1:${SEARXNG_PORT}" \
AUTH_ENABLED="true" \
LOCALHOST_BYPASS="false" \
APP_PORT="$ODYSSEUS_PORT" \
ODYSSEUS_MODE="server" \
"$ODYSSEUS_BIN" \
  >> "$LOG_DIR/odysseus.log" 2>&1 &
ODYSSEUS_PID=$!
log "Odysseus server PID: $ODYSSEUS_PID"

notify "Starting Odysseus..."

# ── Wait for server to accept connections (max 3 min) ────────────────────────
READY=0
for i in $(seq 1 180); do
  if ! kill -0 "$ODYSSEUS_PID" 2>/dev/null; then
    die_gui "Odysseus crashed on startup.\nCheck: $LOG_DIR/odysseus.log"
  fi
  if /usr/bin/curl -sf --max-time 2 "$ODYSSEUS_URL" >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 1
done

if [[ "$READY" != "1" ]]; then
  die_gui "Odysseus server did not start in time.\nCheck: $LOG_DIR/odysseus.log"
fi
log "Server ready on $ODYSSEUS_URL"

# ── Check SearXNG ─────────────────────────────────────────────────────────────
if ! kill -0 "$SEARXNG_PID" 2>/dev/null; then
  log "Warning: SearXNG exited — web search unavailable"
  notify "Warning: SearXNG failed to start. Web search may be unavailable."
fi

# ── Start GUI via nested Odysseus.app PyInstaller BUNDLE ─────────────────────
# The inner bundle is Odysseus.app (inside Resources/) — macOS Dock uses the
# folder name, so it must NOT be Odysseus.app (GUI).
GUI_APP="$RES/Odysseus.app"
[[ -d "$GUI_APP" ]] || die_gui "App bundle corrupted.\nMissing: $GUI_APP"

# Write env file — BUNDLE reads this in _run_gui() via _load_env_file()
# (LSEnvironment only sets ODYSSEUS_MODE; other vars come from here)
GUI_ENV="$DATA_DIR/.gui.env"
cat > "$GUI_ENV" <<GUIENV
ODYSSEUS_DATA="$DATA_DIR"
APP_PORT="$ODYSSEUS_PORT"
ODYSSEUS_APP_NAME="$APP_NAME"
GUIENV
chmod 600 "$GUI_ENV"

log "Starting Odysseus GUI..."
rm -f "$DATA_DIR/logs/.gui.pid" 2>/dev/null || true

# Launch GUI binary directly with ODYSSEUS_MODE=gui set explicitly.
# Do NOT use "open -a" — it does not reliably pass LSEnvironment, causing
# the GUI binary to default to server mode and crash (No module 'uvicorn').
# Auto-detect binary name (Odysseus or OdysseusGUI depending on build).
GUI_BIN="$GUI_APP/Contents/MacOS/Odysseus"
[[ -x "$GUI_BIN" ]] || die_gui "GUI binary missing at $GUI_BIN"

ODYSSEUS_DATA="$DATA_DIR" \
APP_PORT="$ODYSSEUS_PORT" \
ODYSSEUS_APP_NAME="$APP_NAME" \
ODYSSEUS_MODE="gui" \
"$GUI_BIN" \
  >> "$LOG_DIR/odysseus-gui.log" 2>&1 &
GUI_PID=$!

# Give it 2 seconds to confirm it started
sleep 2
if ! kill -0 "$GUI_PID" 2>/dev/null; then
  tail -5 "$LOG_DIR/odysseus-gui.log" >> "$LOG_DIR/launcher.log" 2>/dev/null || true
  die_gui "Odysseus window failed to open.\nCheck: $LOG_DIR/odysseus-gui.log"
fi
log "Odysseus GUI PID: $GUI_PID"

# ── Startup monitor (30s) ─────────────────────────────────────────────────────
for i in $(seq 1 30); do
  if ! kill -0 "$ODYSSEUS_PID" 2>/dev/null; then
    die_gui "Odysseus server crashed.\nCheck: $LOG_DIR/odysseus.log"
  fi
  if [[ -n "$GUI_PID" ]] && ! kill -0 "$GUI_PID" 2>/dev/null; then
    tail -5 "$LOG_DIR/odysseus-gui.log" >> "$LOG_DIR/launcher.log" 2>/dev/null || true
    die_gui "Odysseus window failed to open.\nCheck: $LOG_DIR/odysseus-gui.log"
  fi
  sleep 1
done
log "Startup monitor complete"

# ── Wait for GUI to close — EXIT trap calls cleanup() ────────────────────────
wait "$GUI_PID" 2>/dev/null || true
log "GUI exited — shutting down"
