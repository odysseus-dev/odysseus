#!/bin/bash
# Replace persisted ChatGPT subscription with 9router before Agent Server loads settings.
set -euo pipefail
python3 /opt/odysseus/bootstrap_native_llm.py
exec tini -- /usr/local/bin/openhands-agent-server "$@"
