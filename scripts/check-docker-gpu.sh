#!/usr/bin/env bash
# check-docker-gpu.sh — Diagnostic and optional setup helper for NVIDIA Docker GPU access.
#
# Default mode is READ-ONLY — does not install packages, modify config, or restart Docker.
# The Odysseus app never calls this script automatically.
#
# USAGE
#   scripts/check-docker-gpu.sh                              # read-only diagnostics (default)
#   scripts/check-docker-gpu.sh --enable-nvidia-overlay     # also write COMPOSE_FILE to .env
#   scripts/check-docker-gpu.sh --print-install-commands    # show OS-specific commands, don't run
#   scripts/check-docker-gpu.sh --install-nvidia-toolkit    # install toolkit (Ubuntu/Debian only)
#   scripts/check-docker-gpu.sh --install-nvidia-toolkit --enable-nvidia-overlay
#   scripts/check-docker-gpu.sh --install-nvidia-toolkit --enable-nvidia-overlay --yes
#   scripts/check-docker-gpu.sh --help

MODE="check"
OPT_YES=0
OPT_ENABLE_OVERLAY=0
_GPU_PASSTHROUGH_OK=0

# ─── output helpers ──────────────────────────────────────────────────────────

PASS=0
FAIL=0

_pass() { printf '\033[32m[PASS]\033[0m %s\n' "$*"; PASS=$((PASS + 1)); }
_fail() { printf '\033[31m[FAIL]\033[0m %s\n' "$*"; FAIL=$((FAIL + 1)); }
_info() { printf '\033[34m[INFO]\033[0m %s\n' "$*"; }
_warn() { printf '\033[33m[WARN]\033[0m %s\n' "$*"; }
_step() { printf '\033[36m[STEP]\033[0m %s\n' "$*"; }

_confirm() {
    printf '%s [y/N] ' "$1"
    read -r _ans
    case "${_ans}" in
        [Yy]|[Yy][Ee][Ss]) return 0 ;;
        *) return 1 ;;
    esac
}

# ─── paths ───────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ─── arg parsing ─────────────────────────────────────────────────────────────

_usage() {
    cat <<'USAGE'
Usage: scripts/check-docker-gpu.sh [OPTIONS]

Read-only diagnostic (default — safe to run at any time, installs nothing):
  (no flags)                    Check host nvidia-smi, Docker daemon, and Docker
                                GPU passthrough. Prints PASS/FAIL and next steps.

Informational:
  --print-install-commands      Detect the OS and print recommended NVIDIA
                                Container Toolkit commands without running them.
                                Inspect these before deciding to install.
  --help                        Show this help.

Opt-in .env update (requires .env or .env.example in the repo root):
  --enable-nvidia-overlay       Write COMPOSE_FILE=docker-compose.yml:docker/gpu.nvidia.yml
                                into .env. Creates a timestamped backup first.
                                Blocked if GPU passthrough is not working — fix
                                passthrough first, then re-run. --yes does not
                                override this gate.
                                Never edits .env unless this flag is passed.

Opt-in install (Ubuntu/Debian only, requires sudo):
  --install-nvidia-toolkit      Add NVIDIA's apt repository, install
                                nvidia-container-toolkit, configure the Docker
                                runtime, and optionally restart Docker.
                                Shows all commands and prompts before any
                                privileged action.
  --yes                         Skip confirmation prompts (for use with
                                --install-nvidia-toolkit and/or
                                --enable-nvidia-overlay in automated setups).

Examples:
  # Diagnose GPU passthrough before enabling the NVIDIA compose overlay:
  scripts/check-docker-gpu.sh

  # See what install commands apply to this system without running them:
  scripts/check-docker-gpu.sh --print-install-commands

  # Diagnose and automatically update .env with the NVIDIA overlay:
  scripts/check-docker-gpu.sh --enable-nvidia-overlay

  # Install toolkit interactively, then enable the overlay if it works:
  scripts/check-docker-gpu.sh --install-nvidia-toolkit --enable-nvidia-overlay

  # Full assisted setup without prompts (automated/CI use):
  scripts/check-docker-gpu.sh --install-nvidia-toolkit --enable-nvidia-overlay --yes

After a successful setup, start Odysseus:
  docker compose up -d --build

Full guide: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
USAGE
}

for _arg in "$@"; do
    case "${_arg}" in
        --help|-h)
            _usage
            exit 0
            ;;
        --print-install-commands)
            MODE="print"
            ;;
        --install-nvidia-toolkit)
            MODE="install"
            ;;
        --enable-nvidia-overlay)
            OPT_ENABLE_OVERLAY=1
            ;;
        --yes|-y)
            OPT_YES=1
            ;;
        *)
            printf 'Unknown option: %s\n\n' "${_arg}" >&2
            _usage >&2
            exit 1
            ;;
    esac
done

# ─── OS/distro detection ─────────────────────────────────────────────────────

DISTRO_ID=""
DISTRO_LIKE=""
DISTRO_VERSION=""
DISTRO_ARCH="$(uname -m 2>/dev/null || echo unknown)"

if [ -f /etc/os-release ]; then
    DISTRO_ID="$(grep '^ID=' /etc/os-release | cut -d= -f2 | tr -d '"')"
    DISTRO_LIKE="$(grep '^ID_LIKE=' /etc/os-release | cut -d= -f2 | tr -d '"')"
    DISTRO_VERSION="$(grep '^VERSION_ID=' /etc/os-release | cut -d= -f2 | tr -d '"')"
fi

_is_debian_family() {
    case "${DISTRO_ID}" in
        ubuntu|debian|linuxmint|pop|elementary) return 0 ;;
    esac
    # ID_LIKE can be a space-separated list, e.g. "ubuntu debian"
    case " ${DISTRO_LIKE} " in
        *" debian "*|*" ubuntu "*) return 0 ;;
    esac
    return 1
}

_distro_label() {
    if [ -n "${DISTRO_ID}" ]; then
        printf '%s%s (%s)' \
            "${DISTRO_ID}" \
            "${DISTRO_VERSION:+ ${DISTRO_VERSION}}" \
            "${DISTRO_ARCH}"
    else
        printf 'unknown Linux (%s)' "${DISTRO_ARCH}"
    fi
}

# ─── Ubuntu/Debian install command text ──────────────────────────────────────
# Printed both by --print-install-commands and shown before --install runs.

_debian_install_steps() {
    cat <<'STEPS'

  # 1. Install prerequisites
  sudo apt-get update
  sudo apt-get install -y curl gpg

  # 2. Add NVIDIA's signing key
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

  # 3. Add NVIDIA's apt repository
  curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

  # 4. Install the toolkit
  sudo apt-get update
  sudo apt-get install -y nvidia-container-toolkit

  # 5. Configure the Docker runtime
  sudo nvidia-ctk runtime configure --runtime=docker

  # 6. Restart Docker
  sudo systemctl restart docker

  # 7. Verify
  docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi

STEPS
}

# ─── read-only checks ────────────────────────────────────────────────────────

_check_nvidia_smi() {
    _info "Checking host nvidia-smi..."
    if command -v nvidia-smi >/dev/null 2>&1; then
        if nvidia-smi -L 2>/dev/null | grep -q 'GPU '; then
            _pass "nvidia-smi is working. Detected GPUs:"
            nvidia-smi -L 2>/dev/null | sed 's/^/        /'
        else
            _fail "nvidia-smi found but no GPUs listed — check your NVIDIA driver installation."
        fi
    else
        _fail "nvidia-smi not found — install the NVIDIA driver for your distribution."
        _info "No NVIDIA GPU? Skip this script — the NVIDIA overlay is not needed for CPU-only use."
    fi
    echo
}

# Returns 1 if Docker is unavailable (callers should stop further GPU checks).
_check_docker() {
    _info "Checking Docker..."
    if ! command -v docker >/dev/null 2>&1; then
        _fail "docker not found — install Docker: https://docs.docker.com/engine/install/"
        echo "Cannot continue without Docker."
        return 1
    fi
    if docker info >/dev/null 2>&1; then
        _pass "Docker daemon is running."
    else
        _fail "Docker daemon is not running or current user lacks permission."
        _info "Try: sudo systemctl start docker"
        _info "Or add your user to the docker group: sudo usermod -aG docker \$USER"
        echo "Cannot continue — GPU passthrough test requires a running Docker daemon."
        return 1
    fi
    echo
}

_check_gpu_passthrough() {
    _info "Testing GPU passthrough (may pull image on first run):"
    _info "  docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi"
    echo
    if docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi 2>&1; then
        echo
        _GPU_PASSTHROUGH_OK=1
        _pass "GPU passthrough is working — the NVIDIA compose overlay should work."
        _info "Passthrough means Docker can see your GPU. It does NOT guarantee"
        _info "llama.cpp will use CUDA. If Cookbook logs show:"
        _info "  'Unable to find cudart library'"
        _info "  'Could NOT find CUDAToolkit' / 'CUDA Toolkit not found'"
        _info "  tensors or layers assigned to CPU"
        _info "that is a Cookbook/llama.cpp CUDA build or runtime issue, not a"
        _info "passthrough failure. Re-install the serve engine via"
        _info "Cookbook -> Dependencies to get a CUDA-enabled build."
        if [ "${OPT_ENABLE_OVERLAY}" -eq 0 ]; then
            _info "Enable the overlay in .env with:"
            _info "  scripts/check-docker-gpu.sh --enable-nvidia-overlay"
        fi
    else
        echo
        _fail "GPU passthrough failed. Check these steps in order:"
        echo
        echo "  1. Install NVIDIA Container Toolkit (if not already installed):"
        echo "     Arch:    sudo pacman -S nvidia-container-toolkit"
        echo "     Debian:  sudo apt install nvidia-container-toolkit"
        echo "     Fedora:  sudo dnf install nvidia-container-toolkit"
        echo "     Full guide: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"
        echo
        echo "  2. Configure the Docker runtime:"
        echo "     sudo nvidia-ctk runtime configure --runtime=docker"
        echo
        echo "  3. Restart Docker:"
        echo "     sudo systemctl restart docker"
        echo
        echo "  Then re-run this script to confirm."
        echo
        _warn "Without GPU passthrough, Cookbook will detect the iGPU, another card, or"
        _warn "CPU instead of your NVIDIA GPU — model recommendations will use the wrong VRAM."
        _info "Run with --print-install-commands to see OS-specific commands."
        _info "Run with --install-nvidia-toolkit to install on Ubuntu/Debian."
    fi
    echo
}

# ─── --enable-nvidia-overlay ─────────────────────────────────────────────────

_enable_nvidia_overlay() {
    echo "=== Enabling NVIDIA compose overlay ==="
    echo

    local _env_file="${REPO_ROOT}/.env"
    local _env_example="${REPO_ROOT}/.env.example"
    local _overlay_fragment="docker/gpu.nvidia.yml"
    local _backup_ts
    _backup_ts="$(date +%Y%m%d-%H%M%S)"

    # Ensure .env exists
    if [ ! -f "${_env_file}" ]; then
        if [ -f "${_env_example}" ]; then
            _info ".env not found. .env.example is available."
            local _do_copy=0
            if [ "${OPT_YES}" -eq 1 ]; then
                _do_copy=1
            elif _confirm "Copy .env.example to .env?"; then
                _do_copy=1
            fi
            if [ "${_do_copy}" -eq 1 ]; then
                if ! cp "${_env_example}" "${_env_file}"; then
                    _fail "Failed to copy .env.example to .env."
                    return 1
                fi
                _pass "Copied .env.example to .env."
            else
                _fail ".env is required to set COMPOSE_FILE — aborted."
                return 1
            fi
        else
            _fail ".env not found and .env.example is missing."
            _info "Create a .env file in the repo root, then re-run."
            return 1
        fi
    fi

    # Read current active (uncommented) COMPOSE_FILE value, if any
    local _current_cf
    _current_cf="$(grep '^COMPOSE_FILE=' "${_env_file}" | tail -1 | cut -d= -f2-)"

    # Idempotency check
    if echo "${_current_cf}" | grep -qF "${_overlay_fragment}"; then
        _pass "COMPOSE_FILE already includes the NVIDIA overlay — nothing to change."
        echo
        _info "Start or restart Odysseus to apply:"
        _info "  docker compose up -d --build"
        return 0
    fi

    # Back up .env before any edit
    local _backup="${_env_file}.bak.${_backup_ts}"
    if ! cp "${_env_file}" "${_backup}"; then
        _fail "Failed to create backup of .env — aborting to avoid data loss."
        return 1
    fi
    _info "Backup created: .env.bak.${_backup_ts}"

    local _new_cf=""
    if [ -z "${_current_cf}" ]; then
        # No active COMPOSE_FILE line — append one
        _new_cf="docker-compose.yml:${_overlay_fragment}"
        if ! printf '\nCOMPOSE_FILE=%s\n' "${_new_cf}" >> "${_env_file}"; then
            _fail "Failed to write COMPOSE_FILE to .env."
            return 1
        fi
    else
        # Existing COMPOSE_FILE — append the overlay to the existing value
        _new_cf="${_current_cf}:${_overlay_fragment}"
        local _tmp="${_env_file}.tmp"
        if ! sed "s|^COMPOSE_FILE=.*|COMPOSE_FILE=${_new_cf}|" "${_env_file}" > "${_tmp}"; then
            _fail "Failed to update COMPOSE_FILE in .env."
            rm -f "${_tmp}"
            return 1
        fi
        if ! mv "${_tmp}" "${_env_file}"; then
            _fail "Failed to write updated .env."
            rm -f "${_tmp}"
            return 1
        fi
    fi

    _pass "COMPOSE_FILE set to: ${_new_cf}"
    echo
    _info "Start or restart Odysseus with the NVIDIA overlay:"
    _info "  docker compose up -d --build"
    echo
    _info "To undo, restore the backup:"
    _info "  cp ${_backup} ${_env_file}"
}

# ─── mode: default read-only diagnostic ──────────────────────────────────────

_mode_check() {
    echo "=== Odysseus Docker GPU diagnostic ==="
    echo
    _check_nvidia_smi
    _check_docker || { echo "=== Results: ${PASS} passed, ${FAIL} failed ==="; return 1; }
    _check_gpu_passthrough

    if [ "${OPT_ENABLE_OVERLAY}" -eq 1 ]; then
        if [ "${_GPU_PASSTHROUGH_OK}" -eq 0 ]; then
            # Hard gate: broken passthrough blocks .env edits regardless of --yes.
            # Writing COMPOSE_FILE before passthrough works causes Odysseus to fail
            # at startup, so this is not a prompt — it is a stop.
            _fail "GPU passthrough is not working — .env will not be modified."
            _info "Fix passthrough first, then re-run with --enable-nvidia-overlay:"
            _info "  Ubuntu/Debian: scripts/check-docker-gpu.sh --install-nvidia-toolkit"
            _info "  Other distros: scripts/check-docker-gpu.sh --print-install-commands"
            echo
        else
            _enable_nvidia_overlay
        fi
    fi

    echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
    [ "${FAIL}" -eq 0 ]
}

# ─── mode: --print-install-commands ──────────────────────────────────────────

_mode_print() {
    echo "=== NVIDIA Container Toolkit — install commands ==="
    echo
    _info "Detected system: $(_distro_label)"
    echo

    if _is_debian_family; then
        _info "Ubuntu/Debian — recommended install commands:"
        _debian_install_steps
        _info "After running these, re-run the diagnostic to confirm:"
        _info "  scripts/check-docker-gpu.sh"
    else
        case "${DISTRO_ID}" in
            fedora|rhel|centos|rocky|almalinux)
                _info "Fedora/RHEL — install commands:"
                echo
                echo "  sudo dnf install -y nvidia-container-toolkit"
                echo "  sudo nvidia-ctk runtime configure --runtime=docker"
                echo "  sudo systemctl restart docker"
                echo "  docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi"
                ;;
            opensuse*|sles)
                _info "OpenSUSE/SLES — install commands:"
                echo
                echo "  sudo zypper install nvidia-container-toolkit"
                echo "  sudo nvidia-ctk runtime configure --runtime=docker"
                echo "  sudo systemctl restart docker"
                echo "  docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi"
                ;;
            arch|manjaro|endeavouros)
                _info "Arch Linux — install commands:"
                echo
                echo "  sudo pacman -S nvidia-container-toolkit"
                echo "  sudo nvidia-ctk runtime configure --runtime=docker"
                echo "  sudo systemctl restart docker"
                echo "  docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi"
                ;;
            *)
                _warn "Distro '${DISTRO_ID:-unknown}' is not specifically recognized."
                echo
                echo "  See the full guide for your distribution:"
                echo "  https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"
                ;;
        esac
        echo
        _info "Automated install (--install-nvidia-toolkit) supports Ubuntu/Debian only."
        _info "For other distros, run the commands above manually, then re-run:"
        _info "  scripts/check-docker-gpu.sh"
    fi
}

# ─── mode: --install-nvidia-toolkit ──────────────────────────────────────────

_mode_install() {
    echo "=== NVIDIA Container Toolkit — interactive installer ==="
    echo

    if [ "$(uname -s)" != "Linux" ]; then
        _fail "Install mode is Linux-only. Detected: $(uname -s)"
        exit 1
    fi

    if ! _is_debian_family; then
        _fail "Automated install currently supports Ubuntu/Debian only."
        _info "Detected: $(_distro_label)"
        _info "Run --print-install-commands to see manual steps for your distro."
        exit 1
    fi

    _info "Detected system: $(_distro_label)"
    echo

    echo "This will run the following commands with sudo:"
    _debian_install_steps

    if [ "${OPT_YES}" -eq 0 ]; then
        if ! _confirm "Proceed with the above steps?"; then
            echo "Aborted — nothing was changed."
            exit 0
        fi
        echo
    fi

    # Step 1: prerequisites
    _step "Updating package lists..."
    sudo apt-get update -qq || { _fail "apt-get update failed."; exit 1; }
    _step "Installing prerequisites (curl, gpg)..."
    sudo apt-get install -y curl gpg || { _fail "Failed to install prerequisites."; exit 1; }
    _pass "Prerequisites ready."
    echo

    # Step 2: signing key
    _step "Adding NVIDIA GPG signing key..."
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
        | sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg \
        || { _fail "Failed to add NVIDIA GPG key."; exit 1; }
    _pass "Signing key added."
    echo

    # Step 3: apt repository
    _step "Adding NVIDIA apt repository..."
    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list > /dev/null \
        || { _fail "Failed to add NVIDIA apt repository."; exit 1; }
    _pass "apt repository added."
    echo

    # Step 4: install toolkit
    _step "Installing nvidia-container-toolkit..."
    sudo apt-get update -qq || { _fail "apt-get update failed after adding NVIDIA repo."; exit 1; }
    sudo apt-get install -y nvidia-container-toolkit \
        || { _fail "Failed to install nvidia-container-toolkit."; exit 1; }
    _pass "nvidia-container-toolkit installed."
    echo

    # Step 5: configure Docker runtime
    _step "Configuring Docker runtime..."
    sudo nvidia-ctk runtime configure --runtime=docker \
        || { _fail "nvidia-ctk runtime configure failed."; exit 1; }
    _pass "Docker runtime configured."
    echo

    # Step 6: restart Docker
    _step "A Docker restart is required for the runtime change to take effect."
    local _do_restart=0
    if [ "${OPT_YES}" -eq 1 ]; then
        _do_restart=1
    elif _confirm "Restart Docker now?"; then
        _do_restart=1
    else
        _warn "Docker not restarted."
        _warn "Run 'sudo systemctl restart docker' before testing GPU passthrough."
    fi

    if [ "${_do_restart}" -eq 1 ]; then
        _step "Restarting Docker..."
        if sudo systemctl restart docker; then
            _pass "Docker restarted."
        else
            _fail "Docker restart failed — run: sudo systemctl restart docker"
        fi
    fi
    echo

    # Step 7: verification
    _info "Running GPU passthrough verification..."
    echo
    _check_docker || { echo "=== Results: ${PASS} passed, ${FAIL} failed ==="; exit 1; }
    _check_gpu_passthrough

    # Step 8: enable overlay (only if passthrough verified)
    if [ "${OPT_ENABLE_OVERLAY}" -eq 1 ]; then
        if [ "${_GPU_PASSTHROUGH_OK}" -eq 1 ]; then
            _enable_nvidia_overlay
        else
            _warn "GPU passthrough verification failed — skipping overlay setup."
            _warn "Fix the passthrough issue, then run:"
            _warn "  scripts/check-docker-gpu.sh --enable-nvidia-overlay"
            echo
        fi
    fi

    echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
    [ "${FAIL}" -eq 0 ]
}

# ─── dispatch ────────────────────────────────────────────────────────────────

case "${MODE}" in
    check)   _mode_check ;;
    print)   _mode_print ;;
    install) _mode_install ;;
esac
