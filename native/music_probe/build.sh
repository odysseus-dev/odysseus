#!/usr/bin/env bash
# Build the optional native library probe. The Python fallback does not need it.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
g++ -O2 -std=c++20 \
  $(pkg-config --cflags libavformat libavcodec libavutil) \
  -o music_probe music_probe.cpp \
  $(pkg-config --libs libavformat libavcodec libavutil)
echo "built $(pwd)/music_probe"
