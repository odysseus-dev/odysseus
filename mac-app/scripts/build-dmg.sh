#!/usr/bin/env bash
# Package Odysseus.app into a drag-to-Applications DMG.
#
# Uses `hdiutil` (built into macOS) so there are no external deps. For a
# fancier installer background image, swap in `create-dmg` later.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$HERE/build"
APP="$BUILD_DIR/Odysseus.app"
DMG="$BUILD_DIR/Odysseus.dmg"
STAGE="$BUILD_DIR/dmg-staging"

if [[ ! -d "$APP" ]]; then
    echo "Odysseus.app not found — run build-app.sh first." >&2
    exit 1
fi

rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/Odysseus.app"
# Symlink to /Applications gives users the familiar drag-to-install gesture.
ln -s /Applications "$STAGE/Applications"

hdiutil create \
    -volname "Odysseus" \
    -srcfolder "$STAGE" \
    -ov -format UDZO \
    "$DMG"

rm -rf "$STAGE"
echo "Built: $DMG"
