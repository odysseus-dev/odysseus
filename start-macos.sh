#!/bin/bash
# start-macos.sh — DEPRECATED direct entry point.
#
# Use ./odysseus.sh --launch=native instead. The same code path runs (this
# script just calls into it), and you get the full flag surface for free:
#   ./odysseus.sh --launch=native
#   ./odysseus.sh --update
#   ./odysseus.sh --install-service
#   ./odysseus.sh --port=7900
# Forwarded args are passed through unchanged.
#
# Removal: this shim is deleted in the next cleanup PR. The native path lives
# in odysseus.sh from then on.
#
# When odysseus.sh calls back into this script (for the real native launch
# logic), it sets ODYSSEUS_LEGACY_ENTRY=1 to skip the deprecation banner
# below — otherwise users would see a "start-macos.sh is deprecated" line
# every time they ran the new launcher, which is the opposite of helpful.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Re-entrant: odysseus.sh is delegating to us. Just run the legacy code path.
if [ -n "$ODYSSEUS_LEGACY_ENTRY" ]; then
  exec "$SCRIPT_DIR/scripts/legacy/macos-native.sh" "$@"
fi

echo "▶ start-macos.sh is deprecated — use ./odysseus.sh --launch=native"
echo "  (forwarding to odysseus.sh; this shim will be removed in a future release)"
echo
exec "$SCRIPT_DIR/odysseus.sh" --launch=native "$@"
