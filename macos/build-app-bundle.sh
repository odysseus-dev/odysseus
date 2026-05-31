#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="Odysseus"
BUNDLE="$ROOT/dist/${APP_NAME}.app"
CONTENTS="$BUNDLE/Contents"
MACOS_DIR="$CONTENTS/MacOS"
RESOURCES="$CONTENTS/Resources"
EXECUTABLE="$MACOS_DIR/$APP_NAME"
ICON_KEY=""

mkdir -p "$MACOS_DIR" "$RESOURCES"

cat > "$EXECUTABLE" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
export ODYSSEUS_REPO_ROOT="$ROOT"
exec "\$ODYSSEUS_REPO_ROOT/macos/launch-odysseus.sh"
EOF
chmod +x "$EXECUTABLE"

ICON_SOURCE="$ROOT/static/icon-512.png"
if [[ -f "$ICON_SOURCE" ]] && command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1; then
  ICONSET="$RESOURCES/AppIcon.iconset"
  rm -rf "$ICONSET"
  mkdir -p "$ICONSET"
  sips -z 16 16 "$ICON_SOURCE" --out "$ICONSET/icon_16x16.png" >/dev/null
  sips -z 32 32 "$ICON_SOURCE" --out "$ICONSET/icon_16x16@2x.png" >/dev/null
  sips -z 32 32 "$ICON_SOURCE" --out "$ICONSET/icon_32x32.png" >/dev/null
  sips -z 64 64 "$ICON_SOURCE" --out "$ICONSET/icon_32x32@2x.png" >/dev/null
  sips -z 128 128 "$ICON_SOURCE" --out "$ICONSET/icon_128x128.png" >/dev/null
  sips -z 256 256 "$ICON_SOURCE" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
  sips -z 256 256 "$ICON_SOURCE" --out "$ICONSET/icon_256x256.png" >/dev/null
  sips -z 512 512 "$ICON_SOURCE" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
  sips -z 512 512 "$ICON_SOURCE" --out "$ICONSET/icon_512x512.png" >/dev/null
  sips -z 1024 1024 "$ICON_SOURCE" --out "$ICONSET/icon_512x512@2x.png" >/dev/null
  if iconutil -c icns "$ICONSET" -o "$RESOURCES/AppIcon.icns" >/dev/null 2>&1; then
    ICON_KEY="  <key>CFBundleIconFile</key>
  <string>AppIcon</string>"
  elif sips -s format icns "$ICON_SOURCE" --out "$RESOURCES/AppIcon.icns" >/dev/null 2>&1; then
    ICON_KEY="  <key>CFBundleIconFile</key>
  <string>AppIcon</string>"
  else
    echo "Warning: could not build AppIcon.icns; continuing with the default app icon."
    rm -f "$RESOURCES/AppIcon.icns"
  fi
  rm -rf "$ICONSET"
fi

cat > "$CONTENTS/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>$APP_NAME</string>
  <key>CFBundleIdentifier</key>
  <string>local.odysseus.app</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>$APP_NAME</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>LSUIElement</key>
  <true/>
$ICON_KEY
</dict>
</plist>
EOF

echo "Built $BUNDLE"
