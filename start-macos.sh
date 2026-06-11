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

# Load .env so APP_PORT and APP_BIND are available without re-typing them on
# the command line every run — consistent with how app.py reads them via
# python-dotenv. Variables already set in the shell take priority over .env.
if [ -f .env ]; then
    while IFS='=' read -r key value; do
        [[ "$key" =~ ^[[:space:]]*# ]] && continue
        [[ -z "${key// }" ]] && continue
        value="${value%%#*}"
        value="${value#"${value%%[![:space:]]*}"}"
        value="${value%"${value##*[![:space:]]}"}"
        [ -n "$key" ] && [ -z "${!key+x}" ] && export "$key=$value"
    done < .env
fi

# Shell overrides (ODYSSEUS_PORT / ODYSSEUS_HOST) take top priority, then .env
# values (APP_PORT / APP_BIND), then built-in defaults.
PORT="${ODYSSEUS_PORT:-${APP_PORT:-7860}}"   # 7860, not 7000 — macOS AirPlay Receiver holds 7000.
HOST="${ODYSSEUS_HOST:-${APP_BIND:-127.0.0.1}}" # Set APP_BIND=0.0.0.0 in .env for LAN/Tailscale access.
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

# System dependencies (each installed only if missing, so re-runs stay fast and
# don't re-hit Homebrew over the network):
#    - tmux      : Cookbook runs model downloads/serves in the background
#    - llama.cpp : a prebuilt, Metal-enabled llama-server so Cookbook can serve
#                  GGUF models on the GPU with no compile step
#    - python@3.11 : installed only if no suitable (arm64) Python was found above
#
# tmux and llama.cpp are needed only by Cookbook (local model serving), not to
# boot the core app. So if Homebrew can't install one right now we warn and keep
# going instead of aborting the whole launch. Python is required to build the
# venv, so that one stays fatal (handled by the PY check just below).

# Install a Homebrew formula only if its command isn't already present. A failed
# install warns but does not abort — Cookbook can be set up later.
brew_ensure() {
    if command -v "$1" >/dev/null 2>&1; then
        echo "  ✓ $2 already installed"
        return 0
    fi
    echo "  installing $2…"
    if ! brew install "$2"; then
        echo "  ⚠ Couldn't install $2 right now — Cookbook (local model serving) may be limited."
        echo "    You can install it later with:  brew install $2"
    fi
}

echo "▶ Checking dependencies (Homebrew)…"
if [ -n "$PY" ]; then
    echo "  (using $("$PY" --version 2>&1) at $PY)"
else
    echo "  installing python@3.11…"
    brew install python@3.11 || true
    PY="$(command -v /opt/homebrew/bin/python3.11 || command -v python3.11 || true)"
fi
brew_ensure tmux tmux
brew_ensure llama-server llama.cpp
brew_ensure apfel apfel

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
VENV_PY="./venv/bin/python3"
REQ_HASH="$(md5 -q requirements.txt 2>/dev/null || md5sum requirements.txt | cut -d' ' -f1)"
REQ_HASH_FILE="venv/.requirements_hash"
if [ ! -f "$REQ_HASH_FILE" ] || [ "$REQ_HASH" != "$(cat "$REQ_HASH_FILE" 2>/dev/null)" ]; then
  echo "▶ Installing Python packages (first run downloads a few — can take a few minutes)…"
  "$VENV_PY" -m pip install --quiet --upgrade pip
  # Not --quiet: this is the slow step, so show progress (and any real errors).
  "$VENV_PY" -m pip install -r requirements.txt
  echo "$REQ_HASH" > "$REQ_HASH_FILE"
else
  echo "▶ Python packages up to date — skipping install"
fi

# chromadb-client (HTTP-only) conflicts with the full chromadb package. If
# it got installed (e.g., from an older requirements-optional.txt), remove
# it to prevent ChromaDB from silently failing in HTTP-only mode.
if "$VENV_PY" -m pip show chromadb-client >/dev/null 2>&1; then
    echo "▶ Cleaning up conflicting chromadb-client package…"
    "$VENV_PY" -m pip uninstall -y chromadb-client
    "$VENV_PY" -m pip install --force-reinstall chromadb
fi

# 4. First-run setup: creates data dirs and prints an initial admin password
#    the first time (idempotent — does nothing if already set up). Suppress its
#    manual run hint — we launch the server ourselves just below.
echo "▶ Preparing Odysseus…"
ODYSSEUS_SKIP_RUN_HINT=1 ./venv/bin/python setup.py

# Local provider bootstrap.
#     On Apple Silicon macOS, Apfel is treated as a sibling local model server
#     to Ollama: if Homebrew has it installed, we start its OpenAI-compatible
#     server on the port next to Ollama, since the default port is 11434 and that's busy (because of ollama).
MACHINE_ARCH="$(uname -m)"
APFEL_PID=""
if [ "$MACHINE_ARCH" = "arm64" ]; then
    if command -v apfel >/dev/null 2>&1; then
        APFEL_LOG="${TMPDIR:-/tmp}/odysseus-apfel.log"
        echo "▶ Starting Apfel server in the background on port 11435…"
        echo "  logging to $APFEL_LOG"
        nohup apfel --serve --port 11435 >"$APFEL_LOG" 2>&1 &
        APFEL_PID=$!
    else
        echo "▶ Apfel is not installed (brew formula missing); skipping Apfel server bootstrap."
    fi
else
    echo "▶ Non-ARM macOS detected; skipping Apfel server bootstrap."
fi

# ChromaDB backs the tool index and vector RAG. chromadb ships in the venv, so
# start a local server before launching. Skip when one is already reachable, or
# when CHROMADB_HOST points at a remote host.
CHROMA_PID=""
CHROMA_HOST="${CHROMADB_HOST:-localhost}"   # what the app connects to
CHROMA_PORT="${CHROMADB_PORT:-8100}"
# Bind + probe on IPv4 loopback: the app's "localhost" resolves to 127.0.0.1,
# but binding chroma to the literal "localhost" can land on IPv6 ::1, which the
# app can't then reach. Pin both to 127.0.0.1.
CHROMA_BIN="$(dirname "$VENV_PY")/chroma"
case "$CHROMA_HOST" in
    localhost|127.0.0.1) CHROMA_BIND="127.0.0.1" ;;
    0.0.0.0)             CHROMA_BIND="0.0.0.0" ;;
    *)                   CHROMA_BIND="" ;;   # remote host - don't start locally
esac
if (exec 3<>"/dev/tcp/127.0.0.1/$CHROMA_PORT") 2>/dev/null; then
    echo "▶ ChromaDB already running on 127.0.0.1:$CHROMA_PORT - using it."
elif [ -z "$CHROMA_BIND" ]; then
    echo "▶ CHROMADB_HOST=$CHROMA_HOST is remote - not starting a local ChromaDB."
elif [ -x "$CHROMA_BIN" ]; then
    CHROMA_LOG="${TMPDIR:-/tmp}/odysseus-chromadb.log"
    echo "▶ Starting ChromaDB in the background on $CHROMA_BIND:$CHROMA_PORT…"
    echo "  logging to $CHROMA_LOG"
    nohup "$CHROMA_BIN" run --host "$CHROMA_BIND" --port "$CHROMA_PORT" --path "$PWD/data/chroma" >"$CHROMA_LOG" 2>&1 &
    CHROMA_PID=$!
else
    echo "▶ ChromaDB CLI not found in venv; skipping (tool index will be degraded)."
fi

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
trap '[ -n "$POLLER_PID" ] && kill "$POLLER_PID" 2>/dev/null; [ -n "$APFEL_PID" ] && kill "$APFEL_PID" 2>/dev/null; [ -n "$CHROMA_PID" ] && kill "$CHROMA_PID" 2>/dev/null' EXIT INT TERM

echo
echo "▶ Starting Odysseus — it will open in your browser at $URL"
if [ -n "$TAILSCALE_URL" ]; then
    echo "  Tailscale/LAN URL: $TAILSCALE_URL"
fi
echo "  (this takes a few seconds; press Ctrl+C here to stop)"
echo
"$VENV_PY" -m uvicorn app:app --host "$HOST" --port "$PORT"
