#!/usr/bin/env bash
# Rasterize Resources/AppIcon.svg into the .iconset macOS expects, then
# bundle into Resources/AppIcon.icns. Requires rsvg-convert (brew install
# librsvg) and the built-in iconutil.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SVG="$HERE/Resources/AppIcon.svg"
ICONSET="$HERE/build/AppIcon.iconset"
ICNS="$HERE/Resources/AppIcon.icns"

if ! command -v rsvg-convert >/dev/null; then
    echo "rsvg-convert not found — install with: brew install librsvg" >&2
    exit 1
fi

mkdir -p "$ICONSET"

# Apple's iconset slot list. Each name encodes (logical size, @scale).
# iconutil refuses to build the .icns if any are missing.
declare -a SIZES=(
    "16:icon_16x16.png"
    "32:icon_16x16@2x.png"
    "32:icon_32x32.png"
    "64:icon_32x32@2x.png"
    "128:icon_128x128.png"
    "256:icon_128x128@2x.png"
    "256:icon_256x256.png"
    "512:icon_256x256@2x.png"
    "512:icon_512x512.png"
    "1024:icon_512x512@2x.png"
)

for entry in "${SIZES[@]}"; do
    px="${entry%%:*}"
    name="${entry##*:}"
    rsvg-convert -w "$px" -h "$px" "$SVG" -o "$ICONSET/$name"
done

iconutil -c icns "$ICONSET" -o "$ICNS"
echo "Built $ICNS"
