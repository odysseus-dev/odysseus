#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${ODYSSEUS_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PID_FILE="$ROOT/data/odysseus.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "No Odysseus PID file found."
  exit 0
fi

PID="$(cat "$PID_FILE" 2>/dev/null || true)"
if [[ -z "$PID" ]]; then
  rm -f "$PID_FILE"
  echo "Empty PID file removed."
  exit 0
fi

if kill -0 "$PID" >/dev/null 2>&1; then
  kill "$PID"
  echo "Stopped Odysseus process $PID."
else
  echo "Odysseus process $PID is not running."
fi

rm -f "$PID_FILE"
