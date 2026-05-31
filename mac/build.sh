#!/bin/bash
set -e

# Odysseus Mac App Build Script
# Compiles Swift code and packages it into Odysseus.app

echo "=== Building Odysseus macOS App ==="

# Get absolute path of this script's directory
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$(dirname "$DIR")"

BUILD_DIR="$DIR/build"
APP_DIR="$BUILD_DIR/Odysseus.app"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"

# Clean and recreate directories
echo "Creating build folders..."
rm -rf "$BUILD_DIR"
mkdir -p "$MACOS_DIR"
mkdir -p "$RESOURCES_DIR"

# Generate ICNS App Icon using sips and iconutil
echo "Generating macOS AppIcon.icns from logo.png..."
LOGO_FILE="$DIR/logo.png"

if [ -f "$LOGO_FILE" ]; then
    ICONSET_DIR="$BUILD_DIR/AppIcon.iconset"
    mkdir -p "$ICONSET_DIR"
    
    # Resize image to various sizes required for mac app icon
    sips -s format png -z 16 16     "$LOGO_FILE" --out "$ICONSET_DIR/icon_16x16.png"
    sips -s format png -z 32 32     "$LOGO_FILE" --out "$ICONSET_DIR/icon_16x16@2x.png"
    sips -s format png -z 32 32     "$LOGO_FILE" --out "$ICONSET_DIR/icon_32x32.png"
    sips -s format png -z 64 64     "$LOGO_FILE" --out "$ICONSET_DIR/icon_32x32@2x.png"
    sips -s format png -z 128 128   "$LOGO_FILE" --out "$ICONSET_DIR/icon_128x128.png"
    sips -s format png -z 256 256   "$LOGO_FILE" --out "$ICONSET_DIR/icon_128x128@2x.png"
    sips -s format png -z 256 256   "$LOGO_FILE" --out "$ICONSET_DIR/icon_256x256.png"
    sips -s format png -z 512 512   "$LOGO_FILE" --out "$ICONSET_DIR/icon_256x256@2x.png"
    sips -s format png -z 512 512   "$LOGO_FILE" --out "$ICONSET_DIR/icon_512x512.png"
    sips -s format png -z 1024 1024 "$LOGO_FILE" --out "$ICONSET_DIR/icon_512x512@2x.png"
    
    # Package into .icns file
    iconutil -c icns "$ICONSET_DIR" -o "$RESOURCES_DIR/AppIcon.icns"
    rm -rf "$ICONSET_DIR"
    echo "AppIcon.icns generated successfully."
else
    echo "Warning: logo.png not found. App will build without custom icon."
fi

# Copy Info.plist
echo "Copying metadata (Info.plist)..."
cp "$DIR/Info.plist" "$CONTENTS_DIR/Info.plist"

# Compile Swift sources
echo "Compiling Swift files..."
swiftc -O -sdk "$(xcrun --show-sdk-path)" -target arm64-apple-macos13.0 \
  "$DIR/App/ServerManager.swift" \
  "$DIR/App/WebView.swift" \
  "$DIR/App/SettingsView.swift" \
  "$DIR/App/LogView.swift" \
  "$DIR/App/ContentView.swift" \
  "$DIR/App/OdysseusApp.swift" \
  -o "$MACOS_DIR/Odysseus"

echo "=== Build Complete! ==="
echo "Application built at: $APP_DIR"
