#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo ""
echo "=== Odysseus macOS Installer ==="
echo ""
echo "Project: $ROOT"
echo ""

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 was not found."
  echo "Install Python 3.11+ from https://www.python.org/downloads/macos/ or Homebrew, then run this again."
  read -r -p "Press Return to close..."
  exit 1
fi

python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("ERROR: Python 3.11+ is required. Found %s.%s.%s" % sys.version_info[:3])
print("Python OK:", sys.version.split()[0])
PY

if ! command -v tmux >/dev/null 2>&1; then
  echo ""
  echo "WARNING: tmux was not found."
  echo "Cookbook background downloads/model serves use tmux on macOS."
  echo "Install with: brew install tmux"
  echo "Odysseus can still run without it, but some local model operations will be degraded."
fi

if [[ ! -d venv ]]; then
  echo ""
  echo "Creating virtual environment..."
  python3 -m venv venv
fi

source venv/bin/activate

echo ""
echo "Installing Python dependencies..."
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

echo ""
echo "Running first-time setup..."
python setup.py

echo ""
echo "Building Odysseus.app wrapper..."
./macos/build-app-bundle.sh

echo ""
echo "Launching Odysseus..."
./macos/launch-odysseus.sh

echo ""
echo "Done. Daily launcher: dist/Odysseus.app"
echo ""
read -r -p "Press Return to close..."
