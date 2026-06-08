#!/bin/bash

# dynamically locate the repository root folder relative to this script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )" || exit 1
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)" || exit 1

cd "$REPO_DIR" || exit 1

# detect and activate uv or standard venv if present
if [ -d ".venv" ]; then
    # shellcheck source=/dev/null
    source .venv/bin/activate
elif [ -d "venv" ]; then
    # shellcheck source=/dev/null
    source venv/bin/activate
fi

# run setup configuration checks
python setup.py

# spin up background process to launch the web instance
(sleep 1.5 && xdg-open http://127.0.0.1:7000) &

# run core application server
python -m uvicorn app:app --host 127.0.0.1 --port 7000
