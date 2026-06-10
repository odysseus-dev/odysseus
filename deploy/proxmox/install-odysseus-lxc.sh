#!/usr/bin/env bash
set -Eeuo pipefail

# Odysseus LXC installer for Proxmox-oriented Debian/Ubuntu containers.
# This script is repository-local and designed to be ported into
# community-scripts/ProxmoxVED and then upstream.

ODYSSEUS_REPO_DEFAULT="https://github.com/pewdiepie-archdaemon/odysseus.git"
ODYSSEUS_DIR_DEFAULT="/opt/odysseus"
ODYSSEUS_ENV_FILE_DEFAULT=".env"

APP_PORT="${APP_PORT:-7000}"
INSTALL_OLLAMA="${INSTALL_OLLAMA:-false}"        # true|false
AUTO_PULL_MODELS="${AUTO_PULL_MODELS:-false}"    # true|false
MODEL_TIER="${MODEL_TIER:-cpu}"                  # cpu|gpu_modest|gpu_high
ODYSSEUS_REPO="${ODYSSEUS_REPO:-$ODYSSEUS_REPO_DEFAULT}"
ODYSSEUS_DIR="${ODYSSEUS_DIR:-$ODYSSEUS_DIR_DEFAULT}"
ODYSSEUS_ENV_FILE="${ODYSSEUS_ENV_FILE:-$ODYSSEUS_ENV_FILE_DEFAULT}"

log() { printf '[odysseus-install] %s\n' "$*"; }
die() { printf '[odysseus-install] ERROR: %s\n' "$*" >&2; exit 1; }

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    die "Run as root."
  fi
}

detect_os() {
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
  else
    die "/etc/os-release missing."
  fi
  case "${ID:-}" in
    debian|ubuntu) ;;
    *) die "Unsupported OS: ${ID:-unknown}. Use Debian/Ubuntu LXC." ;;
  esac
}

install_prereqs() {
  log "Installing prerequisites..."
  apt-get update -y
  apt-get install -y --no-install-recommends \
    ca-certificates curl git gnupg lsb-release jq zstd
}

install_docker() {
  if command -v docker >/dev/null 2>&1; then
    log "Docker already present."
  else
    log "Installing Docker..."
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/${ID}/gpg" | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${ID} \
      ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y --no-install-recommends docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
  fi
}

clone_or_update_repo() {
  if [[ -d "${ODYSSEUS_DIR}/.git" ]]; then
    log "Updating existing repo at ${ODYSSEUS_DIR}..."
    git -C "${ODYSSEUS_DIR}" fetch --all --tags
    git -C "${ODYSSEUS_DIR}" pull --ff-only
  else
    log "Cloning Odysseus repo to ${ODYSSEUS_DIR}..."
    git clone "${ODYSSEUS_REPO}" "${ODYSSEUS_DIR}"
  fi
}

prepare_env() {
  cd "${ODYSSEUS_DIR}"
  if [[ ! -f "${ODYSSEUS_ENV_FILE}" ]]; then
    if [[ -f .env.example ]]; then
      cp .env.example "${ODYSSEUS_ENV_FILE}"
      log "Copied .env.example -> ${ODYSSEUS_ENV_FILE}"
    else
      touch "${ODYSSEUS_ENV_FILE}"
      log "Created ${ODYSSEUS_ENV_FILE}"
    fi
  fi
  if ! grep -q '^AUTH_ENABLED=' "${ODYSSEUS_ENV_FILE}"; then
    printf '\nAUTH_ENABLED=true\n' >> "${ODYSSEUS_ENV_FILE}"
  fi
}

run_compose() {
  cd "${ODYSSEUS_DIR}"
  log "Starting Odysseus stack with Docker Compose..."
  local compose_args=(-f docker-compose.yml)
  if [[ "${INSTALL_OLLAMA}" == "true" ]]; then
    cat >docker-compose.proxmox-ollama.yml <<'EOF'
services:
  odysseus:
    extra_hosts:
      - "host.docker.internal:host-gateway"
EOF
    compose_args+=(-f docker-compose.proxmox-ollama.yml)
  fi
  docker compose "${compose_args[@]}" up -d --build
}

install_model_helper() {
  local helper_src="${ODYSSEUS_DIR}/deploy/proxmox/model-profile-helper.sh"
  local helper_dst="/usr/local/bin/odysseus-model-profile"
  [[ -f "${helper_src}" ]] || die "Missing helper at ${helper_src}"
  install -m 0755 "${helper_src}" "${helper_dst}"
}

install_ollama_if_requested() {
  if [[ "${INSTALL_OLLAMA}" != "true" ]]; then
    return 0
  fi
  if command -v ollama >/dev/null 2>&1; then
    log "Ollama already installed."
  else
    log "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
  fi
  configure_ollama_for_docker_access
}

configure_ollama_for_docker_access() {
  log "Configuring Ollama to listen on the LXC network interface..."
  install -d /etc/systemd/system/ollama.service.d
  cat >/etc/systemd/system/ollama.service.d/odysseus.conf <<'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
EOF
  systemctl daemon-reload
  systemctl enable --now ollama
  systemctl restart ollama
  log "Ollama will be reachable from the Odysseus container at http://host.docker.internal:11434/v1"
}

run_model_profile_helper() {
  local helper="/usr/local/bin/odysseus-model-profile"
  local args=(--tier "${MODEL_TIER}")
  if [[ "${AUTO_PULL_MODELS}" == "true" ]]; then
    args+=(--pull-models)
  fi
  if [[ "${INSTALL_OLLAMA}" == "true" ]]; then
    args+=(--expect-ollama)
  fi
  "${helper}" "${args[@]}"
}

print_post_install() {
  local ip
  ip="$(hostname -I | awk '{print $1}')"
  cat <<EOF

Odysseus install complete.

Access:
  http://${ip}:${APP_PORT}

Stack:
  - odysseus
  - chromadb
  - searxng
  - ntfy

Security defaults:
  - AUTH_ENABLED=true in ${ODYSSEUS_DIR}/${ODYSSEUS_ENV_FILE}
  - Keep this private-by-default behind LAN/VPN.
  - For internet exposure, place behind HTTPS reverse proxy.

Useful checks:
  cd ${ODYSSEUS_DIR} && docker compose ps
  cd ${ODYSSEUS_DIR} && docker compose logs --tail=120 odysseus

Model profile helper:
  odysseus-model-profile --tier ${MODEL_TIER}
EOF
}

main() {
  require_root
  detect_os
  install_prereqs
  install_docker
  clone_or_update_repo
  prepare_env
  run_compose
  install_model_helper
  install_ollama_if_requested
  run_model_profile_helper
  print_post_install
}

main "$@"
