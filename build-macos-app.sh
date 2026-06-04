#!/bin/bash
# Build a standalone macOS app bundle for Odysseus.
#
# Produces:
#   dist/Odysseus.app   - native AppKit/WebKit shell with the runtime bundled
#   dist/Odysseus.dmg   - drag-to-Applications installer image
#
# The app bundle carries its own runtime payload, copies that payload into
# Application Support on first launch, and then runs the local backend from
# there so it does not depend on the repo staying in place.
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="Odysseus"
DIST="$REPO_DIR/dist"
APP="$DIST/$APP_NAME.app"
PORT="${ODYSSEUS_PORT:-7860}"
RUNTIME_VERSION="${RUNTIME_VERSION:-$(date -u +%Y%m%d%H%M%S)}"
BUILDER_PY="${BUILDER_PY:-$REPO_DIR/venv/bin/python3}"
MACOS_MIN_VERSION="${MACOS_MIN_VERSION:-11.0}"
BUILD_ARCH="${BUILD_ARCH:-$(uname -m)}"
SWIFT_TARGET="${SWIFT_TARGET:-$BUILD_ARCH-apple-macosx$MACOS_MIN_VERSION}"
APP_SIGN_IDENTITY="${APP_SIGN_IDENTITY:--}"

if [ ! -x "$BUILDER_PY" ]; then
  BUILDER_PY="$(command -v python3 || true)"
fi

if [ -z "$BUILDER_PY" ] || [ ! -x "$BUILDER_PY" ]; then
  echo "✗ No usable Python 3 interpreter found for packaging."
  exit 1
fi

if ! command -v xcrun >/dev/null 2>&1; then
  echo '✗ Xcode command line tools are required (`xcrun` not found).'
  exit 1
fi

case "$BUILD_ARCH" in
  arm64|x86_64)
    ;;
  *)
    echo "✗ Unsupported macOS build architecture: $BUILD_ARCH"
    echo "  Set BUILD_ARCH=arm64 or BUILD_ARCH=x86_64 if you need to override detection."
    exit 1
    ;;
esac

echo "Building $APP_NAME.app"
echo "  port:        $PORT"
echo "  runtime ver: $RUNTIME_VERSION"
echo "  builder py:  $BUILDER_PY"
echo "  arch:        $BUILD_ARCH"
echo "  target:      $SWIFT_TARGET"
echo "  min macOS:   $MACOS_MIN_VERSION"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

BUILD_ROOT="$(mktemp -d /private/tmp/odysseus-macos-build.XXXXXX)"
RUNTIME_STAGE="$BUILD_ROOT/runtime"
mkdir -p "$RUNTIME_STAGE"

PYTHON_VERSION="$("$BUILDER_PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PYTHON_FRAMEWORK_VERSION="$PYTHON_VERSION"
PYTHON_FRAMEWORK_DIR="$("$BUILDER_PY" -c 'import sys; print(sys.base_prefix)')"
PYTHON_FRAMEWORK_NAME="$(basename "$(dirname "$(dirname "$PYTHON_FRAMEWORK_DIR")")")"
PYTHON_FRAMEWORK_STAGE="$RUNTIME_STAGE/python-framework/$PYTHON_FRAMEWORK_NAME/Versions/$PYTHON_FRAMEWORK_VERSION"
PYTHON_BIN_SOURCE="$PYTHON_FRAMEWORK_DIR/bin/python$PYTHON_FRAMEWORK_VERSION"
PYTHON_DYLIB_SOURCE="$(otool -L "$PYTHON_BIN_SOURCE" | awk 'NR==2 {print $1}')"

if [ ! -x "$PYTHON_BIN_SOURCE" ]; then
  echo "✗ Missing base Python executable: $PYTHON_BIN_SOURCE"
  exit 1
fi

cleanup() {
  rm -rf "$BUILD_ROOT"
}
trap cleanup EXIT

# Copy the project into a self-contained runtime payload.
rsync -a \
  --exclude '.git' \
  --exclude 'dist' \
  --exclude '/build' \
  --exclude 'logs' \
  --exclude 'data' \
  --exclude '.DS_Store' \
  --exclude '__pycache__' \
  --exclude '/.pytest_cache' \
  --exclude '.env' \
  "$REPO_DIR"/ "$RUNTIME_STAGE"/

printf '%s\n' "$RUNTIME_VERSION" > "$RUNTIME_STAGE/runtime-version.txt"

mkdir -p "$(dirname "$PYTHON_FRAMEWORK_STAGE")"
rsync -a "$PYTHON_FRAMEWORK_DIR"/ "$PYTHON_FRAMEWORK_STAGE"/

rm -f \
  "$RUNTIME_STAGE/venv/bin/python" \
  "$RUNTIME_STAGE/venv/bin/python3" \
  "$RUNTIME_STAGE/venv/bin/python$PYTHON_VERSION"
cp "$PYTHON_BIN_SOURCE" "$RUNTIME_STAGE/venv/bin/python$PYTHON_VERSION"
chmod +x "$RUNTIME_STAGE/venv/bin/python$PYTHON_VERSION"
ln -s "python$PYTHON_VERSION" "$RUNTIME_STAGE/venv/bin/python3"
ln -s "python$PYTHON_VERSION" "$RUNTIME_STAGE/venv/bin/python"

PYTHON_DYLIB_REL="@executable_path/../../python-framework/$PYTHON_FRAMEWORK_NAME/Versions/$PYTHON_FRAMEWORK_VERSION/Python"
install_name_tool -change "$PYTHON_DYLIB_SOURCE" "$PYTHON_DYLIB_REL" "$RUNTIME_STAGE/venv/bin/python$PYTHON_VERSION"
install_name_tool -id "@rpath/$PYTHON_FRAMEWORK_NAME/Versions/$PYTHON_FRAMEWORK_VERSION/Python" "$PYTHON_FRAMEWORK_STAGE/Python"

# ── Icon (best effort) ────────────────────────────────────────────────────────
if [ -f "$REPO_DIR/docs/odysseus.jpg" ] && command -v sips >/dev/null 2>&1; then
  TMPIMG="$(mktemp -d)"
  sips -c 720 720 "$REPO_DIR/docs/odysseus.jpg" --out "$TMPIMG/sq.png" >/dev/null 2>&1 || cp "$REPO_DIR/docs/odysseus.jpg" "$TMPIMG/sq.png"
  sips -z 512 512 "$TMPIMG/sq.png" --out "$TMPIMG/icon.png" >/dev/null 2>&1
  if sips -s format icns "$TMPIMG/icon.png" --out "$APP/Contents/Resources/odysseus.icns" >/dev/null 2>&1; then
    echo "  icon:        odysseus.icns"
  else
    echo "  icon:        (skipped - conversion failed)"
  fi
  rm -rf "$TMPIMG"
else
  echo "  icon:        (skipped - no docs/odysseus.jpg)"
fi

# ── Info.plist ────────────────────────────────────────────────────────────────
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>             <string>$APP_NAME</string>
    <key>CFBundleDisplayName</key>      <string>$APP_NAME</string>
    <key>CFBundleIdentifier</key>       <string>com.odysseus.app</string>
    <key>CFBundleVersion</key>          <string>1.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundlePackageType</key>      <string>APPL</string>
    <key>CFBundleExecutable</key>       <string>$APP_NAME</string>
    <key>CFBundleIconFile</key>         <string>odysseus</string>
    <key>LSMinimumSystemVersion</key>   <string>$MACOS_MIN_VERSION</string>
    <key>NSHighResolutionCapable</key>  <true/>
    <key>LSUIElement</key>              <false/>
    <key>OdysseusPort</key>             <string>$PORT</string>
    <key>OdysseusRuntimeVersion</key>   <string>$RUNTIME_VERSION</string>
</dict>
</plist>
PLIST

# ── Compile the native app shell ──────────────────────────────────────────────
SWIFT_SRC="$REPO_DIR/macos/OdysseusApp.swift"
if [ ! -f "$SWIFT_SRC" ]; then
  echo "✗ Missing macOS app source: $SWIFT_SRC"
  exit 1
fi

echo "▶ Compiling native app shell…"
"$BUILDER_PY" -c 'import sys; print(sys.version)'
SWIFT_SDK="$(xcrun --sdk macosx --show-sdk-path)"
SWIFT_CACHE="$BUILD_ROOT/swift-module-cache"
mkdir -p "$SWIFT_CACHE"
xcrun swiftc \
  "$SWIFT_SRC" \
  -O \
  -target "$SWIFT_TARGET" \
  -sdk "$SWIFT_SDK" \
  -module-cache-path "$SWIFT_CACHE" \
  -framework Cocoa \
  -framework WebKit \
  -o "$APP/Contents/MacOS/$APP_NAME"
chmod +x "$APP/Contents/MacOS/$APP_NAME"

# ── Bundle the runtime payload ────────────────────────────────────────────────
echo "▶ Bundling runtime payload…"
mkdir -p "$APP/Contents/Resources/runtime"
ditto "$RUNTIME_STAGE" "$APP/Contents/Resources/runtime"

if command -v codesign >/dev/null 2>&1; then
  echo "▶ Code signing bundled Python runtime…"
  codesign --force --deep --sign "$APP_SIGN_IDENTITY" "$APP/Contents/Resources/runtime/python-framework/$PYTHON_FRAMEWORK_NAME/Versions/$PYTHON_FRAMEWORK_VERSION"
  codesign --force --sign "$APP_SIGN_IDENTITY" "$APP/Contents/Resources/runtime/venv/bin/python$PYTHON_VERSION"
  echo "▶ Code signing app bundle…"
  codesign --force --deep --sign "$APP_SIGN_IDENTITY" "$APP"
else
  echo "  codesign:    skipped (codesign unavailable here)"
fi

touch "$APP"

# ── DMG ──────────────────────────────────────────────────────────────────────
echo "Packaging dist/$APP_NAME.dmg"
STAGE="$(mktemp -d /private/tmp/odysseus-dmg.XXXXXX)"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DIST/$APP_NAME.dmg"
DMG_STATUS="skipped"
if ! command -v hdiutil >/dev/null 2>&1; then
  echo "  dmg:        skipped (hdiutil unavailable)"
elif hdiutil create -volname "$APP_NAME" -srcfolder "$STAGE" -ov -format UDZO "$DIST/$APP_NAME.dmg" >/dev/null 2>&1; then
  echo "  dmg:        created"
  DMG_STATUS="created"
else
  echo "  dmg:        skipped (hdiutil failed)"
  rm -f "$DIST/$APP_NAME.dmg"
fi
rm -rf "$STAGE"

echo
echo "Done:"
echo "  $APP"
if [ "$DMG_STATUS" = "created" ]; then
  echo "  $DIST/$APP_NAME.dmg"
fi
echo
echo "Run it:  open '$APP'"
if [ "$DMG_STATUS" = "created" ]; then
  echo "Install:  open '$DIST/$APP_NAME.dmg'  (drag Odysseus to Applications)"
else
  echo "Install:  copy '$APP' into /Applications"
fi
