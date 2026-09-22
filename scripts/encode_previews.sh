#!/usr/bin/env bash
# Encode a source screen-recording (.mkv) into web-optimized preview clips for
# the landing page: website/<name>.webm (VP9) + website/<name>.mp4 (H.264).
#
#   ./encode_previews.sh <input> <name> [max_secs]
#
# - scales to 720p height (even dims), strips audio, yuv420p for broad support
# - if the clip is longer than max_secs (default 30), it's sped up to fit, so
#   loops stay snappy instead of dragging
# - MP4 gets +faststart so it starts without downloading the whole file
set -euo pipefail

IN="${1:?input file}"
NAME="${2:?output basename}"
MAX="${3:-30}"
OUT_DIR="$(cd "$(dirname "$0")/../website" && pwd)"

dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$IN" | cut -d. -f1)
dur=${dur:-0}
SPEED_VF=""
if [ "$dur" -gt "$MAX" ] && [ "$MAX" -gt 0 ]; then
  # setpts factor <1 speeds up; e.g. 30/60 = 0.5x duration => 2x speed
  factor=$(awk "BEGIN{printf \"%.4f\", $MAX/$dur}")
  SPEED_VF="setpts=${factor}*PTS,"
  echo "  ($IN is ${dur}s > ${MAX}s -> speeding up x$(awk "BEGIN{printf \"%.2f\", $dur/$MAX}"))"
fi
VF="${SPEED_VF}scale=-2:720:flags=lanczos"

echo "  -> $NAME.webm"
ffmpeg -y -loglevel error -i "$IN" -an -vf "$VF" \
  -c:v libvpx-vp9 -b:v 0 -crf 34 -row-mt 1 -deadline good -cpu-used 2 \
  -pix_fmt yuv420p "$OUT_DIR/$NAME.webm"

echo "  -> $NAME.mp4"
ffmpeg -y -loglevel error -i "$IN" -an -vf "$VF" \
  -c:v libx264 -crf 25 -preset slow -pix_fmt yuv420p \
  -movflags +faststart "$OUT_DIR/$NAME.mp4"

ls -lh "$OUT_DIR/$NAME.webm" "$OUT_DIR/$NAME.mp4" | awk '{print "    "$5"\t"$9}'
