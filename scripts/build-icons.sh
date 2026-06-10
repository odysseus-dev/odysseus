#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/assets/app-icon.svg"
OUT="$ROOT/dist/icons"
BG="${ODYSSEUS_ICON_BG:-#111111}"
TARGET="${1:-all}"

has() { command -v "$1" >/dev/null 2>&1; }
die() { echo "$*" >&2; exit 1; }

# Tool checks are target-specific; Linux writes SVG and needs no renderer.
require_cmd() { has "$1" || die "$2"; }
require_imagemagick() {
  IMG="$(command -v magick || command -v convert || true)"
  [ -n "$IMG" ] || die "ImageMagick is required to build Windows .ico files."
}

case "$TARGET" in
  macos|windows|linux|png|all) ;;
  -h|--help) echo "Usage: $0 [macos|windows|linux|png|all]"; exit 0 ;;
  *) echo "Usage: $0 [macos|windows|linux|png|all]" >&2; exit 1 ;;
esac

[ -f "$SRC" ] || die "Missing icon source: $SRC"
mkdir -p "$OUT"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Validate required tools
case "$TARGET" in
  macos)
    require_cmd rsvg-convert "rsvg-convert is required to render PNG-based icons. Install librsvg."
    require_cmd iconutil "iconutil is required to build macOS .icns files."
    ;;
  windows)
    require_cmd rsvg-convert "rsvg-convert is required to render PNG-based icons. Install librsvg."
    require_imagemagick
    ;;
  png)
    require_cmd rsvg-convert "rsvg-convert is required to render PNG-based icons. Install librsvg."
    ;;
  all) ;;
esac

# Compose the final app icon SVG from the source boat mark and adds a background.
write_svg() {
  cat > "$1" <<SVG
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">
  <defs><filter id="s" x="-20%" y="-20%" width="140%" height="150%"><feDropShadow dx="0" dy="26" stdDeviation="30" flood-color="#020617" flood-opacity="0.45"/></filter></defs>
  <rect x="96" y="96" width="832" height="832" rx="181" fill="$BG"/>
  <g filter="url(#s)" transform="translate(113.456 113.456) scale(24.909)">
SVG
  sed '1d;$d' "$SRC" >> "$1"
  printf '  </g>\n</svg>\n' >> "$1"
}

# Render one PNG size from the composed SVG.
render_png() {
  write_svg "$TMP/icon.svg"
  rsvg-convert -w "$1" -h "$1" "$TMP/icon.svg" -o "$2"
}

# Build only the requested target.
case "$TARGET" in
  linux)
    write_svg "$OUT/odysseus.svg"
    ;;
  png)
    render_png 1024 "$OUT/odysseus-1024.png"
    ;;
  macos)
    ICONSET="$TMP/odysseus.iconset"
    mkdir -p "$ICONSET"
    for spec in \
      16:icon_16x16.png 32:icon_16x16@2x.png 32:icon_32x32.png 64:icon_32x32@2x.png \
      128:icon_128x128.png 256:icon_128x128@2x.png 256:icon_256x256.png \
      512:icon_256x256@2x.png 512:icon_512x512.png 1024:icon_512x512@2x.png
    do
      size="${spec%%:*}"
      name="${spec#*:}"
      render_png "$size" "$ICONSET/$name"
    done
    iconutil -c icns "$ICONSET" -o "$OUT/odysseus.icns"
    ;;
  windows)
    files=()
    for size in 16 24 32 48 64 128 256; do
      render_png "$size" "$TMP/odysseus-$size.png"
      files+=("$TMP/odysseus-$size.png")
    done
    "$IMG" "${files[@]}" "$OUT/odysseus.ico"
    ;;
  all)
    "$0" linux
    "$0" macos || echo "Skipping macOS .icns."
    "$0" windows || echo "Skipping Windows .ico."
    ;;
esac

echo "Built $TARGET icon target in $OUT"
