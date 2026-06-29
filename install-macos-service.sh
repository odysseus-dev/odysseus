#!/bin/bash
# Install Odysseus as a macOS LaunchAgent so it starts automatically on login.
#
#   ./install-macos-service.sh
#
# What this does:
#   1. Runs start-macos.sh to install dependencies and set up the venv (if needed).
#   2. Writes a LaunchAgent plist to ~/Library/LaunchAgents/.
#   3. Loads (starts) the service immediately.
#
# After install, Odysseus starts automatically every time you log in.
# The web UI is at http://127.0.0.1:7860 (or APP_PORT / ODYSSEUS_PORT if overridden).
#
# Uninstall:
#   launchctl unload ~/Library/LaunchAgents/com.odysseus.app.plist
#   rm ~/Library/LaunchAgents/com.odysseus.app.plist
#
# Manage the service after install:
#   launchctl stop  com.odysseus.app   # stop now
#   launchctl start com.odysseus.app   # start now
#   cat ~/Library/Logs/Odysseus/odysseus.log   # view logs
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

# ── Load .env for APP_PORT / APP_BIND (same logic as start-macos.sh) ──
if [ -f .env ]; then
    while IFS='=' read -r key value; do
        [[ "$key" =~ ^[[:space:]]*# ]] && continue
        [[ -z "${key// }" ]] && continue
        value="${value%%#*}"
        value="${value#"${value%%[![:space:]]*}"}"
        value="${value%"${value##*[![:space:]]}"}"
        [ -n "$key" ] && [ -z "${!key+x}" ] && export "$key=$value"
    done < .env
fi

PORT="${ODYSSEUS_PORT:-${APP_PORT:-7860}}"
HOST="${ODYSSEUS_HOST:-${APP_BIND:-127.0.0.1}}"

PLIST_LABEL="com.odysseus.app"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_FILE="$PLIST_DIR/$PLIST_LABEL.plist"
LOG_DIR="$HOME/Library/Logs/Odysseus"
VENV_UVICORN="$REPO_DIR/venv/bin/uvicorn"

echo "▶ Odysseus — macOS service installer"
echo "  install dir: $REPO_DIR"
echo "  port:        $PORT"
echo "  bind host:   $HOST"
echo ""

# ── 1. Run start-macos.sh to install deps / set up venv ──
# ODYSSEUS_NO_OPEN=1 skips the auto-browser-open since we're headless here.
echo "▶ Running start-macos.sh to set up dependencies (this may take a few minutes on first run)…"
ODYSSEUS_NO_OPEN=1 "$REPO_DIR/start-macos.sh" &
SETUP_PID=$!

# Wait for start-macos.sh to finish setup, then kill the server it started.
# We only need the setup steps; the LaunchAgent will manage the server process.
wait "$SETUP_PID" || true
# start-macos.sh keeps the server running in the foreground — kill any uvicorn
# it started so the LaunchAgent can own the process.
pkill -f "uvicorn app:app" 2>/dev/null || true
sleep 1

if [ ! -x "$VENV_UVICORN" ]; then
    echo "✗ Setup did not produce a venv at $REPO_DIR/venv."
    echo "  Try running ./start-macos.sh manually to diagnose the issue."
    exit 1
fi
echo "▶ Setup complete."
echo ""

# ── 2. Create log directory ──
mkdir -p "$LOG_DIR"

# ── 3. Write the LaunchAgent plist ──
mkdir -p "$PLIST_DIR"
echo "▶ Writing LaunchAgent plist to $PLIST_FILE…"

cat > "$PLIST_FILE" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${VENV_UVICORN}</string>
        <string>app:app</string>
        <string>--host</string>
        <string>${HOST}</string>
        <string>--port</string>
        <string>${PORT}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${REPO_DIR}</string>

    <!-- Start automatically when the user logs in -->
    <key>RunAtLoad</key>
    <true/>

    <!-- Restart automatically if the process crashes -->
    <key>KeepAlive</key>
    <true/>

    <!-- Log stdout/stderr to ~/Library/Logs/Odysseus/ -->
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/odysseus.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/odysseus.log</string>

    <!-- Throttle restart attempts (seconds between automatic restarts) -->
    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
PLIST

echo "  written."
echo ""

# ── 4. Unload any existing instance, then load the new one ──
if launchctl list | grep -q "$PLIST_LABEL" 2>/dev/null; then
    echo "▶ Stopping existing Odysseus service…"
    launchctl unload "$PLIST_FILE" 2>/dev/null || true
fi

echo "▶ Loading Odysseus service (will start immediately and on every login)…"
launchctl load "$PLIST_FILE"

# Give the process a moment to start.
sleep 3

# ── 5. Verify it's running ──
URL_HOST="$HOST"
[ "$URL_HOST" = "0.0.0.0" ] || [ "$URL_HOST" = "::" ] && URL_HOST="127.0.0.1"
URL="http://$URL_HOST:$PORT"

echo ""
if launchctl list | grep -q "$PLIST_LABEL"; then
    echo "  ✓ Odysseus service is loaded."
else
    echo "  ⚠ Service may not have started — check logs:"
    echo "    cat $LOG_DIR/odysseus.log"
fi

echo ""
echo "┌──────────────────────────────────────────────────────────────────────┐"
echo "│  Odysseus is installed as a macOS service.                           │"
echo "│                                                                      │"
printf "│  Web UI:      %-54s │\n" "$URL"
echo "│  Logs:        $LOG_DIR/odysseus.log"
echo "│                                                                      │"
echo "│  It starts automatically every time you log in.                     │"
echo "│                                                                      │"
echo "│  To stop now:        launchctl stop  $PLIST_LABEL   │"
echo "│  To start now:       launchctl start $PLIST_LABEL   │"
echo "│  To uninstall:       launchctl unload $PLIST_FILE"
echo "│                      rm $PLIST_FILE"
echo "└──────────────────────────────────────────────────────────────────────┘"
echo ""
echo "First login password is in the log:"
echo "  grep -i 'admin password' $LOG_DIR/odysseus.log | tail -1"
