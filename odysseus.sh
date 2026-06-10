#!/usr/bin/env bash
# odysseus — one launcher for every platform-native install path.
#
#   ./odysseus.sh --launch=native
#   ./odysseus.sh --launch=docker
#   ./odysseus.sh --update
#   ./odysseus.sh --port=7900 --host=0.0.0.0 --launch=native
#   ./odysseus.sh --add-to-path
#
# On macOS this is the script you'll want. On Linux it works the same way.
# On Windows, use odysseus.ps1 — the flag surface is identical.
#
# Old entry points (start-macos.sh, install-service.sh, update_windows.bat)
# are now thin shims that call into this script.

set -e

# Follow symlinks so that `odysseus` (a symlink in ~/.local/bin or
# /opt/homebrew/bin) resolves to the real install dir. Without this,
# the launcher would compute REPO_DIR as the symlink's directory
# and fail to find app.py, the venv, etc.
SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
  DIR=$(cd -P "$(dirname "$SOURCE")" && pwd)
  SOURCE=$(readlink "$SOURCE")
  [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
REPO_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
cd "$REPO_DIR"

# ── Defaults ──────────────────────────────────────────────────────────────
LAUNCH="native"          # native | docker | docker-nvidia | docker-amd
UPDATE=0
ADD_PATH=0
REMOVE_PATH=0
INSTALL_SERVICE=0
UNINSTALL_SERVICE=0
PORT="${ODYSSEUS_PORT:-${APP_PORT:-7000}}"
HOST="${ODYSSEUS_HOST:-${APP_BIND:-127.0.0.1}}"
DOCKER_NO_OPEN=0
PACKAGE_MAC=0

usage() {
  cat <<EOF
odysseus — one launcher for every platform-native install path.

Usage: odysseus [flags]

Launch mode (default: native):
  --launch=native           Run the app directly on this machine (venv + uvicorn).
                            The right choice on macOS — keeps GPU/Metal access.
  --launch=docker           docker compose up (auto-detects GPU overlay).
  --launch=docker-nvidia    Force the NVIDIA GPU overlay.
  --launch=docker-amd       Force the AMD/ROCm GPU overlay (Linux only).

Lifecycle:
  --update                  git pull + reinstall Python deps (only if requirements.txt
                            changed) + rebuild Docker images (when --launch=docker*).
                            Safe to re-run.
  --add-to-path             Symlink odysseus into ~/.local/bin and ensure that dir is
                            on PATH in ~/.zshrc / ~/.bashrc, so 'odysseus' works
                            from anywhere.
  --remove-from-path        Reverse the above.

Service (auto-start at login):
  --install-service         Install the platform auto-start agent:
                              • macOS:  ~/Library/LaunchAgents/com.odysseus.ui.plist
                              • Linux:  /etc/systemd/system/odysseus-ui.service
                            Runs in the background and survives reboots.
  --uninstall-service       Remove the auto-start agent (leaves data/ + venv/ alone).

Server:
  --port=N                  Override the port (default: 7000; 7860 on macOS by
                            default since AirPlay Receiver holds 7000).
  --host=H                  Override the bind address (default: 127.0.0.1).
                            Use 0.0.0.0 to expose on LAN/Tailscale.

Docker:
  --no-open                 Don't open the browser when the server is ready.

Packaging (macOS):
  --package-mac             Build dist/Odysseus.app + dist/Odysseus.dmg from
                            the current repo. Re-run after moving the repo.

Examples:
  ./odysseus.sh                          # launch native, default port
  ./odysseus.sh --port=7900              # launch native on 7900
  ./odysseus.sh --launch=docker          # docker compose up (auto GPU)
  ./odysseus.sh --update                 # pull + reinstall + rebuild
  ./odysseus.sh --install-service        # auto-start at login
  ./odysseus.sh --add-to-path            # 'odysseus' from anywhere
EOF
}

# ── Arg parsing (long-form only — keeps the surface small) ────────────────
while [ $# -gt 0 ]; do
  case "$1" in
    --launch=*)        LAUNCH="${1#*=}" ;;
    --launch)          LAUNCH="$2"; shift ;;
    --update)          UPDATE=1 ;;
    --add-to-path)     ADD_PATH=1 ;;
    --remove-from-path) REMOVE_PATH=1 ;;
    --install-service) INSTALL_SERVICE=1 ;;
    --uninstall-service) UNINSTALL_SERVICE=1 ;;
    --port=*)          PORT="${1#*=}" ;;
    --port)            PORT="$2"; shift ;;
    --host=*)          HOST="${1#*=}" ;;
    --host)            HOST="$2"; shift ;;
    --no-open)         DOCKER_NO_OPEN=1 ;;
    --package-mac)     PACKAGE_MAC=1 ;;
    -h|--help)         usage; exit 0 ;;
    *)                 echo "Unknown flag: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

# ── Helpers ───────────────────────────────────────────────────────────────
is_macos() { [ "$(uname -s)" = "Darwin" ]; }
is_linux() { [ "$(uname -s)" = "Linux" ]; }

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "✗ Required command not found: $1" >&2
    echo "  Install it, then re-run: $0 $*" >&2
    exit 1
  fi
}

# ── Dispatch ──────────────────────────────────────────────────────────────
case "$LAUNCH" in
  native) ;;
  docker|docker-nvidia|docker-amd) ;;
  *) echo "✗ Unknown --launch value: $LAUNCH" >&2; usage; exit 2 ;;
esac

# --add-to-path: drop a symlink and a one-line shell hook so `odysseus`
# works from any directory. Reversible with --remove-from-path.

if [ "$ADD_PATH" = "1" ]; then
  BIN_DIR="$HOME/.local/bin"
  mkdir -p "$BIN_DIR"
  ln -sf "$REPO_DIR/odysseus.sh" "$BIN_DIR/odysseus"
  ln -sf "$REPO_DIR/odysseus.sh" "$BIN_DIR/odysseus.sh"
  for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
    [ -f "$rc" ] || continue
    if ! grep -qF "$BIN_DIR" "$rc" 2>/dev/null; then
      # $HOME below is intentionally literal — the user's shell expands it
      # on source, which is the right behaviour for a portable rc file.
      # shellcheck disable=SC2016
      printf '\n# Added by Odysseus: make `odysseus` work from anywhere\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
      echo "  ✓ added $BIN_DIR to PATH in $rc"
    fi
  done
  echo "✓ odysseus is now available. Open a new shell or run:  export PATH=\"\$HOME/.local/bin:\$PATH\""
  exit 0
fi

if [ "$REMOVE_PATH" = "1" ]; then
  rm -f "$HOME/.local/bin/odysseus" "$HOME/.local/bin/odysseus.sh"
  for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
    [ -f "$rc" ] || continue
    # Remove the Odysseus-added block.
    if grep -q "Added by Odysseus" "$rc" 2>/dev/null; then
      # Delete the marker line + the export below it.
      python3 - "$rc" <<'PY' 2>/dev/null || true
import sys, re
p = sys.argv[1]
try:
    with open(p) as f: txt = f.read()
except OSError:
    sys.exit(0)
out = re.sub(r'\n*# Added by Odysseus[^\n]*\nexport PATH="\$HOME/\.local/bin:\$PATH"\n*', '\n', txt)
with open(p, 'w') as f: f.write(out)
PY
      echo "  ✓ removed Odysseus PATH entry from $rc"
    fi
  done
  echo "✓ odysseus removed from PATH"
  exit 0
fi

# --install-service / --uninstall-service: defer to platform script.
if [ "$INSTALL_SERVICE" = "1" ]; then
  if is_macos; then
    exec "$REPO_DIR/install-macos-service.sh" "$@"
  elif is_linux; then
    exec "$REPO_DIR/install-service.sh" "$@"
  else
    echo "✗ --install-service is only supported on macOS and Linux" >&2
    exit 1
  fi
fi
if [ "$UNINSTALL_SERVICE" = "1" ]; then
  if is_macos; then
    exec "$REPO_DIR/uninstall-macos-service.sh" "$@"
  elif is_linux; then
    echo "(no Linux uninstall script yet — manually: sudo systemctl disable --now odysseus-ui && sudo rm /etc/systemd/system/odysseus-ui.service)"
    exit 0
  else
    echo "✗ --uninstall-service is only supported on macOS and Linux" >&2
    exit 1
  fi
fi

# --update: pull + reinstall. We always make sure venv deps are current when
# requirements.txt changed, then rebuild Docker images if --launch=docker*.
#
# On macOS, if the LaunchAgent is installed, stop it before git pull so the
# running uvicorn doesn't hold files we'll overwrite; restart it after.
if [ "$UPDATE" = "1" ]; then
  AGENT_LAUNCHED=0
  if is_macos; then
    DOMAIN="gui/$(id -u)"
    if launchctl print "$DOMAIN/com.odysseus.ui" >/dev/null 2>&1; then
      echo "▶ stopping launchd agent…"
      launchctl bootout "$DOMAIN/com.odysseus.ui" 2>/dev/null || true
      AGENT_LAUNCHED=1
    fi
  fi

  echo "▶ git pull…"
  git pull --rebase --autostash
  # Re-invoke ourselves so the rest of the launch (native/docker) picks up
  # any code that changed in the pull. The native path re-checks the
  # requirements hash (start-macos.sh / a future native path) so we don't
  # waste time reinstalling when nothing changed.

  if [ "$AGENT_LAUNCHED" = "1" ]; then
    echo "▶ restarting launchd agent…"
    PLIST_PATH="$HOME/Library/LaunchAgents/com.odysseus.ui.plist"
    if [ -f "$PLIST_PATH" ]; then
      launchctl bootstrap "$DOMAIN" "$PLIST_PATH" 2>/dev/null || true
      launchctl enable "$DOMAIN/com.odysseus.ui" 2>/dev/null || true
      launchctl kickstart -k "$DOMAIN/com.odysseus.ui" 2>/dev/null || true
    fi
  fi
fi

# --package-mac: build the .app + .dmg. macOS only.
if [ "$PACKAGE_MAC" = "1" ]; then
  if ! is_macos; then
    echo "✗ --package-mac is macOS only." >&2
    exit 1
  fi
  exec "$REPO_DIR/build-macos-app.sh"
fi

# ── Launch dispatch ───────────────────────────────────────────────────────

# Probe host: 0.0.0.0 / :: aren't connectable for "is the port open?" checks.
PROBE_HOST="$HOST"
case "$PROBE_HOST" in
  0.0.0.0|::) PROBE_HOST="127.0.0.1" ;;
esac

port_in_use() {
  if is_macos || is_linux; then
    (exec 3<>"/dev/tcp/$PROBE_HOST/$PORT") 2>/dev/null
  else
    return 1
  fi
}

# On macOS, AirPlay Receiver holds port 7000; default to 7860 unless the
# user explicitly asked for something else.
if is_macos && [ "$LAUNCH" = "native" ] && [ -z "$ODYSSEUS_PORT" ] && [ -z "$APP_PORT" ] && [ "$PORT" = "7000" ]; then
  PORT=7860
fi

# ── Native launch (the macOS path) ───────────────────────────────────────
if [ "$LAUNCH" = "native" ]; then
  if is_macos && [ -x "$REPO_DIR/scripts/legacy/macos-native.sh" ]; then
    # Delegate to the legacy macOS native launcher (the original
    # start-macos.sh logic, now living in scripts/legacy/ so the public
    # entry point can be a thin shim). The ODYSSEUS_LEGACY_ENTRY=1 marker
    # tells that script to skip the deprecation banner.
    exec env ODYSSEUS_LEGACY_ENTRY=1 \
      ODYSSEUS_REPO_DIR="$REPO_DIR" \
      ODYSSEUS_PORT="$PORT" ODYSSEUS_HOST="$HOST" \
      ODYSSEUS_NO_OPEN="$([ "$DOCKER_NO_OPEN" = "1" ] && echo 1 || echo)" \
      "$REPO_DIR/scripts/legacy/macos-native.sh"
  fi
  if is_linux; then
    if port_in_use; then
      echo "✗ Port $PORT is already in use on $PROBE_HOST. Stop what's using it, or pick another:"
      echo "    $0 --port=7900"
      exit 1
    fi
    PY=""
    for cand in python3.13 python3.12 python3.11 python3; do
      p="$(command -v "$cand" 2>/dev/null)" || continue
      if "$p" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)' 2>/dev/null; then
        PY="$p"; break
      fi
    done
    if [ -z "$PY" ]; then
      echo "✗ Couldn't find a Python 3.11+ to build the environment with."
      echo "  Install one (e.g. sudo apt install python3.11 python3.11-venv) and re-run."
      exit 1
    fi
    [ -d venv ] || "$PY" -m venv venv
    ./venv/bin/python -m pip install --quiet --upgrade pip
    ./venv/bin/python -m pip install -r requirements.txt
    ODYSSEUS_SKIP_RUN_HINT=1 ./venv/bin/python setup.py
    exec ./venv/bin/python -m uvicorn app:app --host "$HOST" --port "$PORT"
  fi
  echo "✗ Native launch on $(uname -s) is not yet supported by odysseus.sh." >&2
  echo "  On macOS, install via the bundled start-macos.sh. On Windows, use odysseus.ps1." >&2
  exit 1
fi

# ── Docker launch ────────────────────────────────────────────────────────
require_cmd docker

DOCKER_COMPOSE_FILE="docker-compose.yml"
case "$LAUNCH" in
  docker-nvidia) DOCKER_COMPOSE_FILE="docker-compose.gpu-nvidia.yml" ;;
  docker-amd)    DOCKER_COMPOSE_FILE="docker-compose.gpu-amd.yml" ;;
  docker)
    # Auto-detect the best overlay. macOS never has GPU passthrough into
    # Docker, so even an M-series Mac falls back to CPU — but at least we
    # say so.
    if is_macos; then
      echo "  ⚠ Docker on macOS runs in a Linux VM with no GPU access — starting CPU-only."
      echo "    For Metal/GPU acceleration, use --launch=native."
    elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
      DOCKER_COMPOSE_FILE="docker-compose.gpu-nvidia.yml"
      echo "  ✓ detected NVIDIA GPU — using GPU overlay"
    elif [ -e /dev/kfd ] && [ -d /dev/dri ] && is_linux; then
      DOCKER_COMPOSE_FILE="docker-compose.gpu-amd.yml"
      echo "  ✓ detected AMD/ROCm GPU — using GPU overlay"
    else
      echo "  ⚠ No GPU detected (nvidia-smi not found, /dev/kfd not present). Starting CPU-only."
      echo "    To override: --launch=docker-nvidia or --launch=docker-amd"
    fi
    ;;
esac

# docker compose v2 vs v1: prefer 'docker compose', fall back to 'docker-compose'.
if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
else
  echo "✗ Neither 'docker compose' nor 'docker-compose' is available." >&2
  exit 1
fi

if [ "$UPDATE" = "1" ]; then
  echo "▶ rebuilding Docker images…"
  "${DC[@]}" -f "$DOCKER_COMPOSE_FILE" build --pull
fi

if [ -n "$APP_PORT" ] || [ "$PORT" != "7000" ]; then
  APP_PORT="$PORT" "${DC[@]}" -f "$DOCKER_COMPOSE_FILE" up -d --build
else
  "${DC[@]}" -f "$DOCKER_COMPOSE_FILE" up -d --build
fi
echo
echo "▶ Odysseus is up. Tailing logs (Ctrl+C to detach)…"
"${DC[@]}" -f "$DOCKER_COMPOSE_FILE" logs -f odysseus
