#!/bin/sh
set -e

if [ -z "$ODYSSEUS_DATA_DIR" ]; then
    echo "ERROR: ODYSSEUS_DATA_DIR is not set. Are you in the Nix devShell?"
    exit 1
fi

if [ -d "$ODYSSEUS_DATA_DIR" ] && [ -f "$ODYSSEUS_DATA_DIR/auth.json" ]; then
    echo "Setup has already been executed."
    echo "To re-run, delete $ODYSSEUS_DATA_DIR/auth.json first."
    exit 0
else
    mkdir -p "$ODYSSEUS_DATA_DIR"
fi

echo "Running Odysseus first-time setup..."
echo "-----------------------------------------------------"

odysseus-setup

echo "-----------------------------------------------------"
echo "Setup complete! Remember your admin username and password."
echo ""
echo "Start the app with:"
echo "  odysseus-run-tmux    (tmux session with Chroma + app)"
echo "  odysseus             (direct start)"
