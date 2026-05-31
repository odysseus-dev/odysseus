#!/usr/bin/env bash
# Build Odysseus.app from the Swift package.
#
# Requirements: Xcode Command Line Tools (`xcode-select --install`) — that's
# enough; no Xcode app needed. Output: build/Odysseus.app.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$HERE/build"
APP="$BUILD_DIR/Odysseus.app"

cd "$HERE"

# Try a universal (arm64+x86_64) build for one-binary-fits-all distribution.
# That path needs full Xcode (xcbuild); without it we fall back to native-arch
# only, which still produces a working .app but only for the host architecture.
UNIVERSAL_ARGS=(--arch arm64 --arch x86_64)
if ! xcrun --find xcbuild >/dev/null 2>&1; then
    echo "Note: full Xcode not detected — building native-arch only."
    echo "      Install Xcode and rerun for a universal binary suitable for distribution."
    UNIVERSAL_ARGS=()
fi

echo "Building Odysseus…"
swift build -c release ${UNIVERSAL_ARGS[@]+"${UNIVERSAL_ARGS[@]}"} --disable-sandbox
BIN_PATH="$(swift build -c release ${UNIVERSAL_ARGS[@]+"${UNIVERSAL_ARGS[@]}"} --show-bin-path)"

echo "Assembling .app bundle at $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cp "$BIN_PATH/Odysseus" "$APP/Contents/MacOS/Odysseus"
cp "$HERE/Info.plist"   "$APP/Contents/Info.plist"

# Bundle resources into Contents/Resources/ so Bundle.main.url(forResource:)
# finds them at runtime. No nested .bundle needed.
if [[ -f "$HERE/Sources/Odysseus/Resources/docker-compose.mac.yml" ]]; then
    cp "$HERE/Sources/Odysseus/Resources/docker-compose.mac.yml" "$APP/Contents/Resources/docker-compose.mac.yml"
fi

# Build the icon if it's not on disk yet (e.g. fresh clone). Skip silently
# when librsvg isn't installed — the .app still launches, just without a
# custom dock icon.
if [[ ! -f "$HERE/Resources/AppIcon.icns" && -f "$HERE/Resources/AppIcon.svg" ]]; then
    if command -v rsvg-convert >/dev/null; then
        "$HERE/scripts/build-icon.sh"
    else
        echo "Note: librsvg not installed — skipping AppIcon build (brew install librsvg)."
    fi
fi
if [[ -f "$HERE/Resources/AppIcon.icns" ]]; then
    cp "$HERE/Resources/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
fi

# Ad-hoc sign so Gatekeeper at least recognizes the bundle. For Notarization
# replace this with `codesign --sign "Developer ID Application: …" --options runtime`
# and feed the result through `notarytool`.
codesign --force --deep --sign - "$APP"

echo
echo "Built: $APP"
echo "Run with: open '$APP'"
