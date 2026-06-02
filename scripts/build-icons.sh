#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/assets/app-icon.svg"
OUT="$ROOT/dist/icons"
BG="${ODYSSEUS_ICON_BG:-#111111}"
TARGET="${1:-all}"

has() { command -v "$1" >/dev/null 2>&1; }
die() { echo "$*" >&2; exit 1; }

case "$TARGET" in
  macos|windows|linux|png|all) ;;
  -h|--help) echo "Usage: $0 [macos|windows|linux|png|all]"; exit 0 ;;
  *) echo "Usage: $0 [macos|windows|linux|png|all]" >&2; exit 1 ;;
esac

[ -f "$SRC" ] || die "Missing icon source: $SRC"
mkdir -p "$OUT"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

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

render_png() {
  has rsvg-convert || die "rsvg-convert is required to render PNG-based icons."
  write_svg "$TMP/icon.svg"
  rsvg-convert -w "$1" -h "$1" "$TMP/icon.svg" -o "$2"
}

case "$TARGET" in
  linux)
    write_svg "$OUT/odysseus.svg"
    ;;
  png)
    render_png 1024 "$OUT/odysseus-1024.png"
    ;;
  macos)
    has iconutil || die "iconutil is required to build macOS .icns files."
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
    has magick || has convert || die "ImageMagick is required to build Windows .ico files."
    IMG="$(command -v magick || command -v convert)"
    files=()
    for size in 16 24 32 48 64 128 256; do
      render_png "$size" "$TMP/odysseus-$size.png"
      files+=("$TMP/odysseus-$size.png")
    done
    "$IMG" "${files[@]}" "$OUT/odysseus.ico"
    ;;
  all)
    "$0" linux
    has iconutil && "$0" macos || echo "Skipping macOS .icns: iconutil not found."
    (has magick || has convert) && "$0" windows || echo "Skipping Windows .ico: ImageMagick not found."
    ;;
esac

echo "Built $TARGET icon target in $OUT"
