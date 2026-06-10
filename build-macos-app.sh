#!/bin/bash
# Compatibility wrapper for the maintained macOS DMG builder.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/packaging/macos/build-dmg.sh" "$@"
