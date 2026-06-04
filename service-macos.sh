#!/bin/bash
# Odysseus — run as a macOS background service (launchd), no terminal needed.
#
#   ./service-macos.sh install    # one-time: full setup + register service + start
#   ./service-macos.sh uninstall  # remove the service entirely
#   ./service-macos.sh start|stop|restart|status|logs
#
# After install the service is a regular launchd agent: it appears in
# System Settings → General → Login Items & Extensions (toggle works), and
# can be controlled with plain launchctl — the start/stop commands here are
# thin wrappers over:
#
#   launchctl enable gui/$UID/com.odysseus.server && launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.odysseus.server.plist
#   launchctl bootout gui/$UID/com.odysseus.server && launchctl disable gui/$UID/com.odysseus.server
#
# macOS counterpart of odysseus-ui.service (systemd). install runs the full
# start-macos.sh setup (Homebrew deps, venv, pip) itself — no prior step
# needed. Host/port come from .env (APP_BIND / APP_PORT), read on every launch.
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.odysseus.server"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/Odysseus"
LOG_FILE="$LOG_DIR/odysseus.log"
DOMAIN="gui/$(id -u)"

# Load .env the same way start-macos.sh does, so the service and the manual
# script agree on host/port. Variables already set in the environment win.
load_env() {
  cd "$REPO_DIR"
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
  PROBE_HOST="$HOST"
  if [ "$PROBE_HOST" = "0.0.0.0" ] || [ "$PROBE_HOST" = "::" ]; then
    PROBE_HOST="127.0.0.1"
  fi
}

loaded() { launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; }

wait_for_server() {
  load_env
  echo "▶ Starting Odysseus service…"
  for _ in $(seq 1 90); do
    if (exec 3<>"/dev/tcp/$PROBE_HOST/$PORT") 2>/dev/null; then
      echo "✓ Odysseus is running at http://$PROBE_HOST:$PORT"
      return 0
    fi
    sleep 1
  done
  echo "⚠ Service started but the server isn't answering on port $PORT yet."
  echo "  Check the log: ./service-macos.sh logs"
}

case "${1:-}" in

  # Internal: what launchd actually executes. Not meant to be run by hand.
  run)
    load_env
    # launchd starts with a minimal PATH; Cookbook spawns tmux/llama-server
    # from Homebrew, so put Homebrew's bin dirs back.
    export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
    exec "$REPO_DIR/venv/bin/python3" -m uvicorn app:app --host "$HOST" --port "$PORT"
    ;;

  install)
    # Stop a running instance first so the setup's port-in-use check doesn't
    # trip over our own service (re-install is the upgrade path).
    loaded && launchctl bootout "$DOMAIN/$LABEL" && sleep 2
    # Full setup via start-macos.sh (Homebrew deps, venv, pip install,
    # first-run setup) — idempotent, fast when everything is already done.
    ODYSSEUS_SETUP_ONLY=1 "$REPO_DIR/start-macos.sh"
    mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>           <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$REPO_DIR/service-macos.sh</string>
        <string>run</string>
    </array>
    <key>WorkingDirectory</key> <string>$REPO_DIR</string>
    <key>RunAtLoad</key>        <true/>
    <key>KeepAlive</key>        <true/>
    <key>ThrottleInterval</key> <integer>10</integer>
    <key>StandardOutPath</key>  <string>$LOG_FILE</string>
    <key>StandardErrorPath</key> <string>$LOG_FILE</string>
</dict>
</plist>
EOF
    echo "✓ Service registered at $PLIST"
    "$0" start
    echo "  It now also appears in System Settings → Login Items & Extensions."
    echo "  Remove it entirely with: ./service-macos.sh uninstall"
    ;;

  uninstall)
    loaded && launchctl bootout "$DOMAIN/$LABEL"
    rm -f "$PLIST"
    # Clear any persisted disabled state so a future install starts clean.
    launchctl enable "$DOMAIN/$LABEL"
    echo "✓ Odysseus service removed."
    ;;

  start)
    if [ ! -f "$PLIST" ]; then
      echo "✗ Service not installed yet. Run: ./service-macos.sh install"
      exit 1
    fi
    launchctl enable "$DOMAIN/$LABEL"
    if loaded; then
      echo "✓ Odysseus service is already running. Use: ./service-macos.sh restart"
      exit 0
    fi
    launchctl bootstrap "$DOMAIN" "$PLIST"
    wait_for_server
    ;;

  stop)
    if loaded; then
      launchctl bootout "$DOMAIN/$LABEL"
      echo "✓ Odysseus service stopped."
    else
      echo "  Odysseus service is not running."
    fi
    # Persist the stop across logins (cleared again by start / the System
    # Settings toggle).
    launchctl disable "$DOMAIN/$LABEL"
    ;;

  restart)
    if loaded; then
      launchctl kickstart -k "$DOMAIN/$LABEL"
      wait_for_server
    else
      "$0" start
    fi
    ;;

  status)
    load_env
    if loaded; then
      pid="$(launchctl print "$DOMAIN/$LABEL" 2>/dev/null | awk '/^\tpid = /{print $3}')"
      echo "● Service: loaded (pid ${pid:-?})"
    elif [ -f "$PLIST" ]; then
      echo "○ Service: installed but not running"
    else
      echo "○ Service: not installed"
    fi
    if (exec 3<>"/dev/tcp/$PROBE_HOST/$PORT") 2>/dev/null; then
      echo "● Server:  answering at http://$PROBE_HOST:$PORT"
    else
      echo "○ Server:  not answering on $PROBE_HOST:$PORT"
    fi
    ;;

  logs)
    if [ ! -f "$LOG_FILE" ]; then
      echo "No log file yet at $LOG_FILE — has the service been started?"
      exit 1
    fi
    exec tail -n 50 -f "$LOG_FILE"
    ;;

  *)
    echo "Usage: ./service-macos.sh {install|uninstall|start|stop|restart|status|logs}"
    exit 1
    ;;
esac
