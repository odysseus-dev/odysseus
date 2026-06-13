#!/bin/sh
set -e

SESSION_NAME="${1:-odysseus}"

if [ -z "$ODYSSEUS_DATA_DIR" ]; then
    echo "ERROR: ODYSSEUS_DATA_DIR is not set. Are you in the Nix devShell?"
    exit 1
fi

CHROMA_DATA_DIR="${CHROMA_DATA_DIR:-$ODYSSEUS_DATA_DIR/chroma}"
CHROMA_PORT="${CHROMA_PORT:-8100}"

if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo "Session '$SESSION_NAME' already exists. Attaching..."
    tmux attach -t "$SESSION_NAME"
    exit 0
fi

echo "Starting Odysseus in tmux session '$SESSION_NAME'..."
echo "  Left pane:  ChromaDB on port $CHROMA_PORT"
echo "  Right pane: Odysseus on port ${APP_PORT:-7000}"
echo ""
echo "Controls:"
echo "  tmux attach -t $SESSION_NAME        # reattach"
echo "  tmux kill-session -t $SESSION_NAME  # stop"
echo ""

tmux new-session -d -s "$SESSION_NAME" \
    "odysseus-chroma run --path $CHROMA_DATA_DIR --host 0.0.0.0 --port $CHROMA_PORT" \; \
    split-window -h "odysseus" \; \
    attach
