#!/bin/bash
# Odysseus — one-command quick start for macOS (Apple Silicon).
#
#   ./start-macos.sh
#
# Installs everything Odysseus needs via Homebrew, sets up a local Python
# environment, and launches the app — so a generic Mac user can run it without
# knowing anything about venvs, pip, or uvicorn. Safe to re-run; it skips work
# that's already done.
#
# Why native (not Docker): Cookbook serves models on whatever machine Odysseus
# runs on, and Docker on macOS is a Linux VM with no access to the Metal GPU.
# Running natively lets Cookbook detect and use your Mac's GPU.
set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

PORT="${ODYSSEUS_PORT:-7860}"   # 7860, not 7000 — macOS AirPlay Receiver holds 7000.
HOST="${ODYSSEUS_HOST:-127.0.0.1}" # Set ODYSSEUS_HOST=0.0.0.0 for LAN/Tailscale access.
PROBE_HOST="$HOST"
if [ "$PROBE_HOST" = "0.0.0.0" ] || [ "$PROBE_HOST" = "::" ]; then
  PROBE_HOST="127.0.0.1"
fi

# Friendly message on any failure — re-running is safe (every step is idempotent).
trap 'echo; echo "✗ Setup failed above. It is safe to re-run ./start-macos.sh."; exit 1' ERR

echo "▶ Odysseus quick start for macOS"

# Fail fast if the port is already taken (e.g. a previous run still running).
if (exec 3<>"/dev/tcp/$PROBE_HOST/$PORT") 2>/dev/null; then
  echo "✗ Port $PORT is already in use on $PROBE_HOST. Stop what's using it, or pick another port:"
  echo "    ODYSSEUS_PORT=7900 ./start-macos.sh"
  exit 1
fi

# 1. Homebrew — the macOS package manager. We can't safely auto-install it
#    (it wants its own interactive confirmation), so point the user at it.
if ! command -v brew >/dev/null 2>&1; then
  echo
  echo "Homebrew is required but not installed. Install it (one command), then re-run this script:"
  echo '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  echo
  echo "More info: https://brew.sh"
  exit 1
fi

# 2. Find a Python 3.11+ to build the environment with.
#    On Apple Silicon we require an *arm64* interpreter (Homebrew's, under
#    /opt/homebrew). A universal2 or x86 Python — e.g. the python.org installer
#    at /usr/local — produces a venv whose compiled extensions get loaded as the
#    wrong architecture when launched from the .app bundle (Cookbook then dies
#    with "incompatible architecture"). So on arm64 we only look under
#    /opt/homebrew and install Homebrew's python@3.11 if it's missing. On Intel
#    (or non-mac) we just use whatever Python 3.11+ is on PATH.
PY=""
if [ "$(uname -m)" = "arm64" ]; then
  cands="/opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11"
else
  cands="python3 python3.13 python3.12 python3.11"
fi
for cand in $cands; do
  p="$(command -v "$cand" 2>/dev/null)" || continue
  if "$p" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)' 2>/dev/null; then
    PY="$p"; break
  fi
done

# System dependencies:
#    - tmux      : Cookbook runs model downloads/serves in the background
#    - llama.cpp : a prebuilt, Metal-enabled llama-server so Cookbook can serve
#                  GGUF models on the GPU with no compile step
#    - python@3.11 : installed only if no suitable (arm64) Python was found above
echo "▶ Installing dependencies (Homebrew)…"
if [ -n "$PY" ]; then
  echo "  (using $("$PY" --version 2>&1) at $PY)"
  brew install tmux llama.cpp
else
  brew install python@3.11 tmux llama.cpp
  PY="$(command -v /opt/homebrew/bin/python3.11 || command -v python3.11 || true)"
fi

if [ -z "$PY" ] || [ ! -x "$PY" ]; then
  echo "✗ Couldn't find a Python 3.11+ to build the environment with."
  echo "  Check: ls /opt/homebrew/bin/python3*  (or install one: brew install python@3.11)"
  exit 1
fi

# 3. Python environment + dependencies (kept inside the repo, in venv/).
#    Named `venv` to match the manual steps and build-macos-app.sh, so the
#    clickable .app reuses this same environment.
if [ ! -d venv ]; then
  echo "▶ Creating Python environment…"
  "$PY" -m venv venv
fi
echo "▶ Installing Python packages (first run downloads a few — can take a few minutes)…"
"$PY" -m pip install --quiet --upgrade pip
# Not --quiet: this is the slow step, so show progress (and any real errors).
"$PY" -m pip install -r requirements.txt

# 4. First-run setup: creates data dirs and prints an initial admin password
#    the first time (idempotent — does nothing if already set up). Suppress its
#    manual run hint — we launch the server ourselves just below.
echo "▶ Preparing Odysseus…"
ODYSSEUS_SKIP_RUN_HINT=1 ./venv/bin/python setup.py

# 5. Launch. Bind to loopback by default; opt into LAN/Tailscale with
#    ODYSSEUS_HOST=0.0.0.0.
URL_HOST="$HOST"
if [ "$URL_HOST" = "0.0.0.0" ] || [ "$URL_HOST" = "::" ]; then
  URL_HOST="127.0.0.1"
fi
URL="http://$URL_HOST:$PORT"
TAILSCALE_URL=""
if [ "$HOST" = "0.0.0.0" ] && command -v tailscale >/dev/null 2>&1; then
  TS_IP="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
  if [ -n "$TS_IP" ]; then
    TAILSCALE_URL="http://$TS_IP:$PORT"
  fi
fi

# Open the browser automatically once the server is accepting connections — so
# the URL isn't lost in the startup logs that keep scrolling. Runs in the
# background and is cleaned up when the server stops. Skip with
# ODYSSEUS_NO_OPEN=1 (e.g. over SSH / headless).
POLLER_PID=""
if [ -z "$ODYSSEUS_NO_OPEN" ] && command -v open >/dev/null 2>&1; then
  (
    for _ in $(seq 1 90); do
      if (exec 3<>"/dev/tcp/$PROBE_HOST/$PORT") 2>/dev/null; then
        printf '\n'
        printf '  ┌────────────────────────────────────────────┐\n'
        printf '  │  ✓ Odysseus is ready — opening your browser  │\n'
        printf '  │     %-40s │\n' "$URL"
        printf '  │     (Press Ctrl+C in this window to stop)    │\n'
        printf '  └────────────────────────────────────────────┘\n\n'
        open "$URL"
        break
      fi
      sleep 1
    done
  ) &
  POLLER_PID=$!
fi

# Setup is done — drop the setup-failure handler, and clean up the background
# opener when the server exits or the user presses Ctrl+C.
trap - ERR
trap '[ -n "$POLLER_PID" ] && kill "$POLLER_PID" 2>/dev/null' EXIT INT TERM

echo
echo "▶ Starting Odysseus — it will open in your browser at $URL"
if [ -n "$TAILSCALE_URL" ]; then
  echo "  Tailscale/LAN URL: $TAILSCALE_URL"
fi
echo "  (this takes a few seconds; press Ctrl+C here to stop)"
echo
"$PY" -m uvicorn app:app --host "$HOST" --port "$PORT"
