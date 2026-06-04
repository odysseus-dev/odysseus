#!/bin/bash
# install-service.sh — DEPRECATED.
#
# Use ./odysseus.sh --install-service instead. Same code path, with the
# same .service file getting written into /etc/systemd/system/.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -n "$ODYSSEUS_LEGACY_ENTRY" ]; then
  # Re-entrant from odysseus.sh — just run the original install script.
  exec "$SCRIPT_DIR/scripts/legacy/linux-systemd-install.sh" "$@"
fi

echo "▶ install-service.sh is deprecated — use ./odysseus.sh --install-service"
echo "  (forwarding; this shim will be removed in a future release)"
echo
exec "$SCRIPT_DIR/odysseus.sh" --install-service "$@"
