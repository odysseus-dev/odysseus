#!/usr/bin/env bash
# uninstall-macos-service.sh — reverse install-macos-service.sh.
#
#   * bootout the LaunchAgent
#   * delete ~/Library/LaunchAgents/com.odysseus.ui.plist
#   * leave ~/Library/Logs/Odysseus/ in place (the user may want to grep them)
#   * leave data/ + venv/ alone (those are not the service's concern)
#
# Idempotent: running when the agent is not installed is a no-op.

set -e

LABEL="com.odysseus.ui"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"

REMOVED=0

# 1. Stop the agent (if running).
if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  echo "▶ stopping $LABEL…"
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  REMOVED=1
fi

# 2. Defensive: also stop a system-domain copy, in case someone installed
#    it that way manually before this script existed.
launchctl bootout "system/$LABEL" 2>/dev/null || true

# 3. Disable so launchd doesn't try to re-resurrect it on next login.
launchctl disable "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl disable "system/$LABEL" 2>/dev/null || true

# 4. Clear any "disabled" override entry that step 3 added. Leaving it
#    in the per-user overrides db means a future --install-service
#    bootstrap will succeed at the launchctl call but the agent will
#    refuse to spawn — surfacing later as a "Bootstrap failed: 5:
#    I/O error" the next time someone tries. enable is the inverse
#    of disable and removes the override entry.
launchctl enable "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl enable "system/$LABEL" 2>/dev/null || true

# 4. Delete the plist.
if [ -f "$PLIST_PATH" ]; then
  echo "▶ removing $PLIST_PATH…"
  rm -f "$PLIST_PATH"
  REMOVED=1
fi

if [ "$REMOVED" = "1" ]; then
  echo "✓ Odysseus auto-start removed."
  echo "  Logs preserved in: $HOME/Library/Logs/Odysseus/"
  echo "  Reinstall: ./odysseus.sh --install-service"
else
  echo "✓ Odysseus auto-start was not installed. Nothing to do."
fi
