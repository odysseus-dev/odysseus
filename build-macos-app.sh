#!/bin/bash
# Build a downloadable macOS launcher app + .dmg for Odysseus.
#
#   ./build-macos-app.sh
#
# Produces:
#   dist/Odysseus.app   — double-click: brings up the FULL local stack
#                         (OrbStack+SearXNG for web search, ChromaDB for the tool
#                         index / RAG, the uvicorn backend) and opens the UI in an
#                         app-style window.
#   dist/Odysseus.dmg   — drag-to-Applications disk image (the downloadable).
#
# This is a *launcher* wrapper: it drives the venv we set up in this repo, it
# does not bundle Python. The install path is baked into the app at build time,
# so rebuild if you move the repo. Override the port with ODYSSEUS_PORT.
#
# The generated launcher prefers the out-of-repo daily launcher
# (~/.local/bin/odysseus-launch) when present — single source of truth on the dev
# machine — and otherwise runs a self-contained full-stack startup, so the .dmg
# still works on a fresh Mac.
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="Odysseus"
INSTALL_DIR="$REPO_DIR"
PORT="${ODYSSEUS_PORT:-7860}"
DIST="$REPO_DIR/dist"
APP="$DIST/$APP_NAME.app"

echo "Building $APP_NAME.app"
echo "  install dir: $INSTALL_DIR"
echo "  port:        $PORT"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# ── Icon (best effort) — center-crop docs/odysseus.jpg to a square .icns ──
if [ -f "$REPO_DIR/docs/odysseus.jpg" ] && command -v sips >/dev/null 2>&1; then
  TMPIMG="$(mktemp -d)"
  # Center-crop to a square, scale to 512 (sips' icns encoder caps at 512), and
  # let sips emit the .icns directly — more robust across macOS versions than
  # building an .iconset by hand.
  sips -c 720 720 "$REPO_DIR/docs/odysseus.jpg" --out "$TMPIMG/sq.png" >/dev/null 2>&1 || cp "$REPO_DIR/docs/odysseus.jpg" "$TMPIMG/sq.png"
  sips -z 512 512 "$TMPIMG/sq.png" --out "$TMPIMG/icon.png" >/dev/null 2>&1
  if sips -s format icns "$TMPIMG/icon.png" --out "$APP/Contents/Resources/odysseus.icns" >/dev/null 2>&1; then
    echo "  icon:        odysseus.icns"
  else
    echo "  icon:        (skipped — conversion failed)"
  fi
  rm -rf "$TMPIMG"
else
  echo "  icon:        (skipped — no docs/odysseus.jpg)"
fi

# ── Info.plist ──
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>            <string>$APP_NAME</string>
    <key>CFBundleDisplayName</key>     <string>$APP_NAME</string>
    <key>CFBundleIdentifier</key>      <string>com.odysseus.launcher.fullstack</string>
    <key>CFBundleVersion</key>         <string>1.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundlePackageType</key>     <string>APPL</string>
    <key>CFBundleExecutable</key>      <string>$APP_NAME</string>
    <key>CFBundleIconFile</key>        <string>odysseus</string>
    <key>LSMinimumSystemVersion</key>  <string>11.0</string>
    <key>NSHighResolutionCapable</key> <true/>
    <key>LSUIElement</key>             <false/>
</dict>
</plist>
PLIST

# ── Launcher executable (placeholders filled below) ──
cat > "$APP/Contents/MacOS/$APP_NAME.tmpl" <<'LAUNCHER'
#!/bin/bash
# Odysseus.app — bring up the FULL local stack, then open the UI in an app window.
#
# On the machine that owns the out-of-repo daily launcher
# (~/.local/bin/odysseus-launch) this just delegates to it, so there is a single
# source of truth. Anywhere else (e.g. a fresh .dmg install) it falls back to a
# self-contained full-stack startup: OrbStack+SearXNG (web search), ChromaDB
# (tool index / RAG) and the uvicorn backend — never the old uvicorn-only path
# that left search dead and the RAG degraded.
set -u

# Single source of truth on the dev machine: prefer the daily launcher if present.
DAILY="$HOME/.local/bin/odysseus-launch"
[ -x "$DAILY" ] && exec "$DAILY"

INSTALL_DIR="__INSTALL_DIR__"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

notify() { /usr/bin/osascript -e "display notification \"$1\" with title \"Odysseus\"" >/dev/null 2>&1; }
die_gui() {
  /usr/bin/osascript -e "display dialog \"$1\" with title \"Odysseus\" buttons {\"OK\"} default button 1 with icon stop" >/dev/null 2>&1
  exit 1
}

cd "$INSTALL_DIR" || die_gui "Odysseus install folder not found: $INSTALL_DIR"

# Port: shell override > APP_PORT in .env > baked default.
PORT="${ODYSSEUS_PORT:-}"
if [ -z "$PORT" ] && [ -f .env ]; then
  PORT="$(grep -E '^[[:space:]]*APP_PORT=' .env 2>/dev/null | tail -1 | cut -d= -f2 | tr -d '[:space:]')"
fi
PORT="${PORT:-__PORT__}"
URL="http://127.0.0.1:${PORT}"

UVICORN="$INSTALL_DIR/venv/bin/uvicorn"
CHROMA_BIN="$INSTALL_DIR/venv/bin/chroma"
CHROMA_PORT="${CHROMADB_PORT:-8100}"
LOG_DIR="$INSTALL_DIR/logs"
mkdir -p "$LOG_DIR"
APP_LOG="$LOG_DIR/odysseus-app.log"
CHROMA_LOG="${TMPDIR:-/tmp}/odysseus-chromadb.log"

[ -x "$UVICORN" ] || die_gui "Odysseus isn't set up yet. Open Terminal and run:

cd $INSTALL_DIR
./start-macos.sh"

port_up() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

# Open the UI in a chrome-less app window (Chromium browsers), else default browser.
open_ui() {
  local b base exe bin
  for b in "Google Chrome" "Microsoft Edge" "Brave Browser" "Chromium"; do
    for base in "/Applications" "$HOME/Applications"; do
      if [ -d "$base/$b.app" ]; then
        exe="$(/usr/bin/defaults read "$base/$b.app/Contents/Info" CFBundleExecutable 2>/dev/null)"
        bin="$base/$b.app/Contents/MacOS/$exe"
        if [ -x "$bin" ]; then
          "$bin" --app="$URL" --new-window >/dev/null 2>&1 &
          return 0
        fi
      fi
    done
  done
  /usr/bin/open "$URL"
}

# Web search needs SearXNG (Docker via OrbStack). Best-effort + non-blocking:
# `open -ga` hands off to launchd and returns at once, so it fires even on the
# fast "already serving" exit path. Skipped cleanly when OrbStack isn't installed.
start_search_stack() {
  command -v orb >/dev/null 2>&1 || return
  if docker info >/dev/null 2>&1; then
    port_up 8080 || docker compose up -d searxng >/dev/null 2>&1   # engine up → ensure searxng
    return
  fi
  /usr/bin/open -ga OrbStack 2>/dev/null || orb start >/dev/null 2>&1 || true
  ( for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 1; done
    port_up 8080 || docker compose up -d searxng >/dev/null 2>&1 ) >/dev/null 2>&1 &
}

# Ensure OrbStack + SearXNG on every launch (incl. the fast path below).
start_search_stack

# Already serving? Just open another app window and exit.
if /usr/bin/curl -s -o /dev/null --max-time 2 "$URL"; then
  open_ui
  exit 0
fi

notify "Starting…"

# Only stop services THIS launcher started.
CHROMA_PID=""
SERVER_PID=""
cleanup() {
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
  [ -n "$CHROMA_PID" ] && kill "$CHROMA_PID" 2>/dev/null
}
trap cleanup EXIT INT TERM

# 1. ChromaDB (tool index + vector RAG). Skip if already reachable.
if port_up "$CHROMA_PORT"; then
  :
elif [ -x "$CHROMA_BIN" ]; then
  nohup "$CHROMA_BIN" run --host 127.0.0.1 --port "$CHROMA_PORT" --path "$INSTALL_DIR/data/chroma" >"$CHROMA_LOG" 2>&1 &
  CHROMA_PID=$!
fi

# 2. FastAPI backend (native arm64 so compiled extensions load correctly).
if [ "$(uname -m)" = "arm64" ]; then
  arch -arm64 "$UVICORN" app:app --host 127.0.0.1 --port "$PORT" >>"$APP_LOG" 2>&1 &
else
  "$UVICORN" app:app --host 127.0.0.1 --port "$PORT" >>"$APP_LOG" 2>&1 &
fi
SERVER_PID=$!

# 3. Wait for readiness (first run downloads an embedding model — allow ~2 min).
READY=0
for _ in $(seq 1 120); do
  /usr/bin/curl -s -o /dev/null --max-time 2 "$URL" && { READY=1; break; }
  kill -0 "$SERVER_PID" 2>/dev/null || die_gui "Odysseus failed to start. Log:
$APP_LOG"
  sleep 1
done

if [ "$READY" = "1" ]; then
  open_ui
else
  notify "Odysseus is taking a while — open $URL once it finishes starting."
fi
wait "$SERVER_PID"
LAUNCHER

sed -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" -e "s|__PORT__|$PORT|g" \
    "$APP/Contents/MacOS/$APP_NAME.tmpl" > "$APP/Contents/MacOS/$APP_NAME"
rm -f "$APP/Contents/MacOS/$APP_NAME.tmpl"
chmod +x "$APP/Contents/MacOS/$APP_NAME"

# Refresh Finder's icon cache for the new bundle.
touch "$APP"

# ── .dmg (drag-to-Applications) ──
echo "Packaging dist/$APP_NAME.dmg"
STAGE="$(mktemp -d)/dmg"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DIST/$APP_NAME.dmg"
hdiutil create -volname "$APP_NAME" -srcfolder "$STAGE" -ov -format UDZO "$DIST/$APP_NAME.dmg" >/dev/null
rm -rf "$STAGE"

echo ""
echo "Done:"
echo "  $APP"
echo "  $DIST/$APP_NAME.dmg"
echo ""
echo "Run it:        open '$APP'"
echo "Install:       open '$DIST/$APP_NAME.dmg'  (drag Odysseus to Applications)"
