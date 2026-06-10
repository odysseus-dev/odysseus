#!/bin/bash
# Build an unsigned Odysseus.app and DMG for macOS.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_NAME="Odysseus"
VERSION="${ODYSSEUS_VERSION:-0.1.0}"
PORT="${ODYSSEUS_PORT:-7860}"
BUILD_DIR="$REPO_DIR/build/packaging/macos"
DIST_DIR="$REPO_DIR/dist/macos"
PAYLOAD_DIR="$BUILD_DIR/payload"
APP="$DIST_DIR/$APP_NAME.app"

rm -rf "$BUILD_DIR" "$APP"
mkdir -p "$PAYLOAD_DIR" "$APP/Contents/MacOS" "$APP/Contents/Resources" "$DIST_DIR"

echo "Staging payload"
rsync -a --delete \
  --exclude ".git" \
  --exclude ".github" \
  --exclude "venv" \
  --exclude ".venv" \
  --exclude "data" \
  --exclude "logs" \
  --exclude "dist" \
  --exclude "build" \
  --exclude "node_modules" \
  --exclude ".pytest_cache" \
  --exclude "__pycache__" \
  --exclude ".env" \
  --exclude "packaging" \
  --exclude "tests" \
  "$REPO_DIR/" "$PAYLOAD_DIR/"

printf '%s\n' "$VERSION" > "$PAYLOAD_DIR/.odysseus-payload-version"
cp -R "$PAYLOAD_DIR" "$APP/Contents/Resources/payload"

if [ -f "$REPO_DIR/docs/odysseus.jpg" ] && command -v sips >/dev/null 2>&1; then
  TMPIMG="$(mktemp -d)"
  sips -c 720 720 "$REPO_DIR/docs/odysseus.jpg" --out "$TMPIMG/sq.png" >/dev/null 2>&1 || cp "$REPO_DIR/docs/odysseus.jpg" "$TMPIMG/sq.png"
  sips -z 512 512 "$TMPIMG/sq.png" --out "$TMPIMG/icon.png" >/dev/null 2>&1
  sips -s format icns "$TMPIMG/icon.png" --out "$APP/Contents/Resources/odysseus.icns" >/dev/null 2>&1 || true
  rm -rf "$TMPIMG"
fi

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$APP_NAME</string>
  <key>CFBundleDisplayName</key><string>$APP_NAME</string>
  <key>CFBundleIdentifier</key><string>com.odysseus.launcher</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>$APP_NAME</string>
  <key>CFBundleIconFile</key><string>odysseus</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/$APP_NAME" <<'LAUNCHER'
#!/bin/bash
set -euo pipefail

APP_SUPPORT="$HOME/Library/Application Support/Odysseus"
APP_DIR="$APP_SUPPORT/app"
LOG_DIR="$APP_SUPPORT/logs"
PAYLOAD_DIR="$(cd "$(dirname "$0")/../Resources/payload" && pwd)"
PAYLOAD_VERSION_FILE="$PAYLOAD_DIR/.odysseus-payload-version"
INSTALLED_VERSION_FILE="$APP_SUPPORT/.odysseus-payload-version"

PORT="${ODYSSEUS_PORT:-${APP_PORT:-7860}}"
HOST="${ODYSSEUS_HOST:-${APP_BIND:-127.0.0.1}}"
URL_HOST="$HOST"
if [ "$URL_HOST" = "0.0.0.0" ] || [ "$URL_HOST" = "::" ]; then
  URL_HOST="127.0.0.1"
fi
URL="http://$URL_HOST:$PORT"
PROBE_URL="http://127.0.0.1:$PORT"

notify() { /usr/bin/osascript -e "display notification \"$1\" with title \"Odysseus\"" >/dev/null 2>&1 || true; }
die_gui() {
  /usr/bin/osascript -e "display dialog \"$1\" with title \"Odysseus\" buttons {\"OK\"} default button 1 with icon stop" >/dev/null 2>&1 || true
  exit 1
}

open_ui() {
  local browser base exe bin
  for browser in "Google Chrome" "Microsoft Edge" "Brave Browser" "Chromium"; do
    for base in "/Applications" "$HOME/Applications"; do
      if [ -d "$base/$browser.app" ]; then
        exe="$(/usr/bin/defaults read "$base/$browser.app/Contents/Info" CFBundleExecutable 2>/dev/null || true)"
        bin="$base/$browser.app/Contents/MacOS/$exe"
        if [ -x "$bin" ]; then
          "$bin" --app="$URL" --new-window >/dev/null 2>&1 &
          return 0
        fi
      fi
    done
  done
  /usr/bin/open "$URL"
}

mkdir -p "$APP_SUPPORT" "$LOG_DIR"

payload_version="$(cat "$PAYLOAD_VERSION_FILE" 2>/dev/null || true)"
installed_version="$(cat "$INSTALLED_VERSION_FILE" 2>/dev/null || true)"
if [ ! -d "$APP_DIR" ] || [ "$payload_version" != "$installed_version" ]; then
  notify "Updating app files"
  mkdir -p "$APP_DIR"
  /usr/bin/rsync -a --delete \
    --exclude "data" \
    --exclude "logs" \
    --exclude "venv" \
    --exclude ".env" \
    "$PAYLOAD_DIR/" "$APP_DIR/"
  printf '%s\n' "$payload_version" > "$INSTALLED_VERSION_FILE"
fi

cd "$APP_DIR" || die_gui "App folder not found: $APP_DIR"

if /usr/bin/curl -s -o /dev/null --max-time 2 "$PROBE_URL/login"; then
  open_ui
  exit 0
fi

PY=""
for candidate in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    path="$(command -v "$candidate")"
    if "$path" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)' >/dev/null 2>&1; then
      PY="$path"
      break
    fi
  fi
done

if [ -z "$PY" ]; then
  die_gui "Python 3.11 or newer is required. Install it with Homebrew or python.org, then reopen Odysseus."
fi

if [ ! -x "./venv/bin/python3" ]; then
  notify "Creating Python environment"
  "$PY" -m venv venv >>"$LOG_DIR/setup.log" 2>&1
fi

VENV_PY="./venv/bin/python3"
notify "Installing dependencies"
"$VENV_PY" -m pip install --quiet --upgrade pip >>"$LOG_DIR/setup.log" 2>&1
"$VENV_PY" -m pip install -r requirements.txt >>"$LOG_DIR/setup.log" 2>&1

notify "Preparing Odysseus"
ODYSSEUS_SKIP_RUN_HINT=1 "$VENV_PY" setup.py >>"$LOG_DIR/setup.log" 2>&1

notify "Starting Odysseus"
"$VENV_PY" -m uvicorn app:app --host "$HOST" --port "$PORT" >>"$LOG_DIR/odysseus.log" 2>&1 &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true; exit 0' TERM INT

READY=0
for _ in $(seq 1 120); do
  if /usr/bin/curl -s -o /dev/null --max-time 2 "$PROBE_URL/login"; then
    READY=1
    break
  fi
  kill -0 "$SERVER_PID" 2>/dev/null || die_gui "Odysseus failed to start. See $LOG_DIR/odysseus.log"
  sleep 1
done

if [ "$READY" = "1" ]; then
  open_ui
else
  notify "Still starting. Open $URL soon."
fi

wait "$SERVER_PID"
LAUNCHER

chmod +x "$APP/Contents/MacOS/$APP_NAME"
touch "$APP"

echo "Packaging DMG"
DMG_STAGE="$(mktemp -d)/dmg"
mkdir -p "$DMG_STAGE"
cp -R "$APP" "$DMG_STAGE/"
ln -s /Applications "$DMG_STAGE/Applications"
rm -f "$DIST_DIR/$APP_NAME.dmg"
hdiutil create -volname "$APP_NAME" -srcfolder "$DMG_STAGE" -ov -format UDZO "$DIST_DIR/$APP_NAME.dmg" >/dev/null
rm -rf "$(dirname "$DMG_STAGE")"

echo "Built $APP"
echo "Built $DIST_DIR/$APP_NAME.dmg"
