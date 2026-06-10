#!/usr/bin/env bash
#
# crabbox.sh — run Odysseus on a fresh, throwaway cloud box in one command.
#
#   ./crabbox.sh test     # warm a box, sync this checkout, run the test suite, tear down
#   ./crabbox.sh serve    # boot Odysseus on a box and print a public URL you can click
#   ./crabbox.sh shell    # drop into an interactive shell on the box
#
# Why this exists
#   Odysseus is a self-hosted AI workspace. Trying it normally means cloning,
#   making a venv, installing deps, and booting uvicorn on *your* machine. This
#   script does all of that on a remote microVM instead, so you can kick the
#   tyres (or run CI) without touching your laptop — and throw the box away when
#   you're done.
#
# How it works
#   - `test`/`shell` go through crabbox (https://github.com/openclaw/crabbox):
#     it warms a box, rsyncs your *working tree* (including uncommitted diff),
#     runs the command, streams output, and releases the lease on exit.
#   - `serve` goes through the islo.dev CLI (https://islo.dev): it spins up a
#     persistent sandbox from this GitHub repo, boots Odysseus, and opens a
#     shareable HTTPS URL straight to the running workspace.
#
#   Both run on islo.dev's sandbox fabric. crabbox is the ephemeral "run the
#   suite" path; islo is the persistent "click the live app" path.
#
# Requirements
#   - ISLO_API_KEY   in your environment (mint one: `islo api-key create <name>`)
#   - crabbox        (brew install openclaw/tap/crabbox)   — for test/shell
#   - islo           (https://islo.dev)                    — for serve
#
set -euo pipefail

# ----------------------------------------------------------------------------
# Config (override via environment)
# ----------------------------------------------------------------------------
IMAGE="${ODYSSEUS_BOX_IMAGE:-docker.io/library/python:3.12-slim}"
VCPUS="${ODYSSEUS_BOX_VCPUS:-4}"
MEMORY_MB="${ODYSSEUS_BOX_MEMORY_MB:-8192}"
DISK_GB="${ODYSSEUS_BOX_DISK_GB:-20}"
PORT="${ODYSSEUS_PORT:-7000}"
SANDBOX="${ODYSSEUS_SANDBOX:-odysseus}"
REPO_SLUG="${ODYSSEUS_REPO:-zozo123/odysseus}"
REPO_BRANCH="${ODYSSEUS_BRANCH:-main}"
# Default test target: a fast, dependency-light slice. Override to run more,
# e.g. ODYSSEUS_TESTS="tests" ./crabbox.sh test  (the full 355-file suite).
TESTS="${ODYSSEUS_TESTS:-tests -q -x -k 'recurrence or static_mime or ordinal or quant_formats or preview'}"

c_blue()  { printf '\033[1;34m%s\033[0m\n' "$*"; }
c_green() { printf '\033[1;32m%s\033[0m\n' "$*"; }
c_red()   { printf '\033[1;31m%s\033[0m\n' "$*" 1>&2; }

require_key() {
  if [[ -z "${ISLO_API_KEY:-}" ]]; then
    c_red "ISLO_API_KEY is not set."
    c_red "Mint one with:  islo api-key create odysseus-demo --output-file islo.key"
    c_red "Then:           export ISLO_API_KEY=\$(cat islo.key)"
    exit 1
  fi
}

require_bin() {
  command -v "$1" >/dev/null 2>&1 || { c_red "missing '$1' — $2"; exit 1; }
}

# Install the real openclaw crabbox if it isn't on PATH, the way https://crabbox.sh
# documents it: Homebrew tap, or a GoReleaser archive from GitHub releases.
ensure_crabbox() {
  command -v crabbox >/dev/null 2>&1 && return 0
  c_blue "> crabbox not found — installing openclaw/crabbox (see https://crabbox.sh) ..."
  if command -v brew >/dev/null 2>&1; then
    brew install openclaw/tap/crabbox && return 0
  fi
  # No Homebrew: grab the GoReleaser archive for this OS/arch.
  local os arch tag ver url tmp
  case "$(uname -s)" in Darwin) os=darwin;; Linux) os=linux;; *) c_red "unsupported OS for auto-install; see https://crabbox.sh"; exit 1;; esac
  case "$(uname -m)" in x86_64|amd64) arch=amd64;; arm64|aarch64) arch=arm64;; *) c_red "unsupported arch $(uname -m)"; exit 1;; esac
  tag="$(curl -fsSL https://api.github.com/repos/openclaw/crabbox/releases/latest | grep -m1 '"tag_name"' | cut -d'"' -f4)"
  ver="${tag#v}"
  url="https://github.com/openclaw/crabbox/releases/download/${tag}/crabbox_${ver}_${os}_${arch}.tar.gz"
  tmp="$(mktemp -d)"
  c_blue "  downloading ${url}"
  curl -fsSL "$url" | tar -xz -C "$tmp" || { c_red "download failed — install manually: https://crabbox.sh"; exit 1; }
  mkdir -p "${CRABBOX_INSTALL_DIR:-$HOME/.local/bin}"
  install -m 0755 "$tmp/crabbox" "${CRABBOX_INSTALL_DIR:-$HOME/.local/bin}/crabbox"
  export PATH="${CRABBOX_INSTALL_DIR:-$HOME/.local/bin}:$PATH"
  rm -rf "$tmp"
  command -v crabbox >/dev/null 2>&1 || { c_red "crabbox still not on PATH — add ${CRABBOX_INSTALL_DIR:-$HOME/.local/bin} to PATH"; exit 1; }
  c_green "OK crabbox $(crabbox --version 2>/dev/null) installed"
}

# Bootstrap commands that turn a bare python:3.12-slim box into a working
# Odysseus install. Kept as a single string so it runs identically under
# crabbox (synced tree) and islo (cloned repo).
bootstrap() {
  cat <<'SH'
set -e
echo "> system deps"
apt-get update -qq && apt-get install -y -qq --no-install-recommends git build-essential >/dev/null
echo "> python deps (this is the slow part)"
pip install --no-cache-dir -q -r requirements.txt
echo "> first-time setup"
python setup.py
SH
}

cmd_test() {
  require_key
  ensure_crabbox
  c_blue "> warming an islo.dev box via crabbox, syncing this checkout, running the suite..."
  # shellcheck disable=SC2086
  crabbox run \
    --provider islo \
    --islo-image "$IMAGE" \
    --islo-vcpus "$VCPUS" \
    --islo-memory-mb "$MEMORY_MB" \
    --islo-disk-gb "$DISK_GB" \
    -- bash -c "$(bootstrap)
echo '> test suite'
python -m pytest $TESTS"
}

cmd_shell() {
  require_key
  ensure_crabbox
  c_blue "> opening an interactive shell on a fresh islo.dev box (synced from this checkout)..."
  crabbox run --provider islo --islo-image "$IMAGE" --keep -- bash -lc "$(bootstrap); exec bash"
}

cmd_serve() {
  require_key
  require_bin islo "install the islo.dev CLI: https://islo.dev"
  local repo_name="${REPO_SLUG##*/}"
  c_blue "> booting Odysseus on a persistent islo.dev sandbox from github://${REPO_SLUG} ..."
  # --user root: islo's default user can't apt-get. We cd into the repo islo
  # cloned at /workspace/<repo> before running the bootstrap.
  # Install + setup + uvicorn run *fully detached* on the box (setsid -f), so this
  # foreground exec returns in seconds. Running the ~80s pip install inline would
  # keep a synchronous exec stream open long enough for it to drop ("Stream error").
  islo use "$SANDBOX" \
    --no-config --run-as-user root \
    --source "github://$REPO_SLUG:$REPO_BRANCH" \
    --image "$IMAGE" --cpu "$VCPUS" --memory "$MEMORY_MB" --disk "$DISK_GB" \
    --env "APP_BIND=0.0.0.0" --env "APP_PORT=$PORT" \
    -- bash -c "cd /workspace/$repo_name 2>/dev/null || cd \$(find /workspace -maxdepth 2 -name requirements.txt -printf '%h' -quit)
setsid -f bash -c '
  apt-get update -qq && apt-get install -y -qq --no-install-recommends git build-essential >/dev/null 2>&1
  pip install --no-cache-dir -q -r requirements.txt >/workspace/boot.log 2>&1
  python setup.py >>/workspace/boot.log 2>&1
  python -m uvicorn app:app --host 0.0.0.0 --port $PORT >>/workspace/boot.log 2>&1
' </dev/null >/dev/null 2>&1
echo 'boot kicked off — installing + launching in background (see /workspace/boot.log)'"
  c_blue "> creating a public share URL for port ${PORT} ..."
  islo share "$SANDBOX" "$PORT" --ttl 24h
  c_green "OK Share URL is live above. Odysseus finishes installing on the box (~90s);"
  c_green "  the URL starts serving once uvicorn binds. Watch progress:"
  c_green "    islo use $SANDBOX --no-config --run-as-user root -- tail -f /workspace/boot.log"
  c_green "  First login: admin password is in that log (grep -i password)."
  c_green "  Tear down when done:  islo rm $SANDBOX"
}

usage() {
  cat <<USAGE
crabbox.sh — run Odysseus on a throwaway cloud box

  ./crabbox.sh test     warm a box, sync this checkout, run the test suite, tear down
  ./crabbox.sh serve    boot Odysseus and print a public URL you can click
  ./crabbox.sh shell    interactive shell on the box (kept until you exit)

Environment overrides:
  ISLO_API_KEY            (required) islo.dev API key
  ODYSSEUS_BOX_IMAGE      container image            (default: $IMAGE)
  ODYSSEUS_BOX_VCPUS      vCPUs                       (default: $VCPUS)
  ODYSSEUS_BOX_MEMORY_MB  memory in MB                (default: $MEMORY_MB)
  ODYSSEUS_TESTS          pytest target/flags         (default: a fast slice; set "tests" for all)
  ODYSSEUS_PORT           serve port                  (default: $PORT)
  ODYSSEUS_SANDBOX        islo sandbox name for serve (default: $SANDBOX)
USAGE
}

case "${1:-}" in
  test)  cmd_test ;;
  serve) cmd_serve ;;
  shell) cmd_shell ;;
  ""|-h|--help|help) usage ;;
  *) c_red "unknown command: $1"; usage; exit 2 ;;
esac
