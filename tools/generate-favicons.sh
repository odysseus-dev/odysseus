#!/usr/bin/env bash
# Generate PNG and ICO favicons from the SVG source using rsvg-convert or ImageMagick.
# Usage: ./tools/generate-favicons.sh path/to/hellaine-logo.svg
set -euo pipefail
SRC="${1:-/static/hellaine-logo.svg}"
OUTDIR="./static"
mkdir -p "$OUTDIR"
# Prefer rsvg-convert if available (from librsvg), otherwise fallback to ImageMagick's convert.
if command -v rsvg-convert >/dev/null 2>&1; then
  rsvg-convert -w 16 -h 16 "$SRC" -o "$OUTDIR/favicon-16x16.png"
  rsvg-convert -w 32 -h 32 "$SRC" -o "$OUTDIR/favicon-32x32.png"
  rsvg-convert -w 48 -h 48 "$SRC" -o "$OUTDIR/favicon-48x48.png"
  rsvg-convert -w 180 -h 180 "$SRC" -o "$OUTDIR/apple-touch-icon.png"
  rsvg-convert -w 192 -h 192 "$SRC" -o "$OUTDIR/android-chrome-192x192.png"
  rsvg-convert -w 512 -h 512 "$SRC" -o "$OUTDIR/android-chrome-512x512.png"
else
  if ! command -v convert >/dev/null 2>&1; then
    echo "Neither rsvg-convert nor ImageMagick's convert are available. Install librsvg2-bin or imagemagick." >&2
    exit 1
  fi
  convert "$SRC" -background none -resize 16x16 "$OUTDIR/favicon-16x16.png"
  convert "$SRC" -background none -resize 32x32 "$OUTDIR/favicon-32x32.png"
  convert "$SRC" -background none -resize 48x48 "$OUTDIR/favicon-48x48.png"
  convert "$SRC" -background none -resize 180x180 "$OUTDIR/apple-touch-icon.png"
  convert "$SRC" -background none -resize 192x192 "$OUTDIR/android-chrome-192x192.png"
  convert "$SRC" -background none -resize 512x512 "$OUTDIR/android-chrome-512x512.png"
fi
# Build a multi-size ICO (16/32/48)
if command -v convert >/dev/null 2>&1; then
  convert "$OUTDIR/favicon-16x16.png" "$OUTDIR/favicon-32x32.png" "$OUTDIR/favicon-48x48.png" "$OUTDIR/favicon.ico"
  echo "Favicons generated in $OUTDIR"
else
  echo "PNG files generated but cannot create .ico — install ImageMagick to build favicon.ico" >&2
fi
