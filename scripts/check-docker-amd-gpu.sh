#!/usr/bin/env bash
# check-docker-amd-gpu.sh - read-only AMD/ROCm Docker passthrough diagnostic.
#
# This script does not install packages, edit .env, or restart Docker. It only
# checks host AMD device nodes, Docker access, and whether a small container can
# see /dev/kfd and /dev/dri. The Odysseus slim image does not include ROCm tools
# such as rocm-smi, so container verification checks devices instead.

set -u

PASS=0
FAIL=0
WARN=0
RENDER_GID=""
VIDEO_GID=""
TEST_IMAGE="${ODYSSEUS_AMD_TEST_IMAGE:-alpine:3.20}"

_pass() { printf '\033[32m[PASS]\033[0m %s\n' "$*"; PASS=$((PASS + 1)); }
_fail() { printf '\033[31m[FAIL]\033[0m %s\n' "$*"; FAIL=$((FAIL + 1)); }
_warn() { printf '\033[33m[WARN]\033[0m %s\n' "$*"; WARN=$((WARN + 1)); }
_info() { printf '\033[34m[INFO]\033[0m %s\n' "$*"; }

_usage() {
    cat <<'USAGE'
Usage: scripts/check-docker-amd-gpu.sh

Read-only AMD/ROCm Docker GPU diagnostic. Installs nothing, edits nothing, and
does not restart Docker.

Checks:
  - host /dev/kfd and /dev/dri/renderD* exist
  - host render group GID for RENDER_GID in .env
  - optional host rocminfo visibility
  - Docker can pass AMD device nodes into a small container

Environment:
  ODYSSEUS_AMD_TEST_IMAGE   Docker image for the passthrough smoke
                            (default: alpine:3.20)
USAGE
}

for _arg in "$@"; do
    case "${_arg}" in
        --help|-h)
            _usage
            exit 0
            ;;
        *)
            printf 'Unknown option: %s\n\n' "${_arg}" >&2
            _usage >&2
            exit 1
            ;;
    esac
done

_find_cmd() {
    if command -v "$1" >/dev/null 2>&1; then
        command -v "$1"
        return 0
    fi
    if [ -x "/opt/rocm/bin/$1" ]; then
        printf '/opt/rocm/bin/%s\n' "$1"
        return 0
    fi
    return 1
}

_check_host_devices() {
    _info "Checking host AMD device nodes..."
    if [ -e /dev/kfd ]; then
        _pass "/dev/kfd exists"
    else
        _fail "/dev/kfd is missing - ROCm kernel driver access is not available."
    fi

    if [ -d /dev/dri ]; then
        _pass "/dev/dri exists"
    else
        _fail "/dev/dri is missing - render devices are not available."
        return
    fi

    render_nodes="$(find /dev/dri -maxdepth 1 -type c -name 'renderD*' -print 2>/dev/null | sort)"
    if [ -n "${render_nodes}" ]; then
        _pass "Render nodes found:"
        printf '%s\n' "${render_nodes}" | sed 's/^/        /'
    else
        _fail "No /dev/dri/renderD* node found."
    fi
    echo
}

_check_groups() {
    _info "Checking host render/video groups..."
    RENDER_GID="$(getent group render | awk -F: '{print $3; exit}')"
    VIDEO_GID="$(getent group video | awk -F: '{print $3; exit}')"

    if [ -n "${RENDER_GID}" ]; then
        _pass "render group GID: ${RENDER_GID}"
    else
        _fail "render group not found - set RENDER_GID manually if your distro uses a different group."
    fi

    if [ -n "${VIDEO_GID}" ]; then
        _pass "video group GID: ${VIDEO_GID}"
    else
        _warn "video group not found. /dev/kfd and renderD* may still be enough on some hosts."
    fi
    echo
}

_check_host_rocm() {
    _info "Checking host ROCm tools..."
    rocminfo_cmd="$(_find_cmd rocminfo || true)"
    if [ -n "${rocminfo_cmd}" ]; then
        if "${rocminfo_cmd}" 2>/dev/null | grep -Eq 'gfx[0-9a-f]+'; then
            _pass "rocminfo works on the host: ${rocminfo_cmd}"
            "${rocminfo_cmd}" 2>/dev/null \
                | grep -E 'Marketing Name:|Name:[[:space:]]+gfx' \
                | head -12 \
                | sed 's/^/        /'
        else
            _warn "rocminfo exists but did not list a gfx target."
        fi
    else
        _warn "rocminfo not found on PATH or /opt/rocm/bin. This does not block Docker passthrough, but host ROCm may be incomplete."
    fi
    echo
}

_check_docker() {
    _info "Checking Docker..."
    if ! command -v docker >/dev/null 2>&1; then
        _fail "docker not found - install Docker first."
        echo
        return 1
    fi
    if docker info >/dev/null 2>&1; then
        _pass "Docker daemon is running."
    else
        _fail "Docker daemon is not running or this user lacks Docker permission."
        echo
        return 1
    fi
    echo
}

_check_docker_passthrough() {
    if [ -z "${RENDER_GID}" ]; then
        _fail "Skipping Docker passthrough smoke because render GID is unknown."
        echo
        return
    fi

    _info "Testing AMD device passthrough with ${TEST_IMAGE} (may pull on first run)..."
    group_args=(--group-add "${RENDER_GID}")
    if [ -n "${VIDEO_GID}" ]; then
        group_args+=(--group-add "${VIDEO_GID}")
    fi

    if docker run --rm \
        --device=/dev/kfd \
        --device=/dev/dri \
        "${group_args[@]}" \
        "${TEST_IMAGE}" \
        sh -lc 'test -e /dev/kfd && test -d /dev/dri && ls /dev/dri/renderD* >/dev/null' \
        >/dev/null 2>&1; then
        _pass "Docker can pass /dev/kfd and /dev/dri render nodes into a container."
    else
        _fail "Docker AMD device passthrough failed."
        _info "Check that Docker can access /dev/kfd and /dev/dri, then retry."
    fi
    echo
}

_print_next_steps() {
    echo "=== Suggested .env values ==="
    if [ -n "${RENDER_GID}" ]; then
        printf 'COMPOSE_FILE=docker-compose.yml:docker/gpu.amd.yml\n'
        printf 'RENDER_GID=%s\n' "${RENDER_GID}"
    else
        printf 'COMPOSE_FILE=docker-compose.yml:docker/gpu.amd.yml\n'
        printf 'RENDER_GID=<numeric render group id>\n'
    fi
    echo
    echo "After restarting Odysseus, verify the slim app container sees devices:"
    echo "  docker compose exec odysseus sh -lc 'test -e /dev/kfd && test -d /dev/dri && ls -l /dev/kfd /dev/dri/renderD*'"
    echo
    echo "Note: rocm-smi/rocminfo are not expected inside the slim Odysseus image."
    echo "Device passthrough is necessary but not sufficient for GPU serving; vLLM and"
    echo "llama.cpp still need ROCm-compatible builds or ROCm-specific Docker images."
}

echo "=== Odysseus AMD Docker GPU diagnostic ==="
echo
_check_host_devices
_check_groups
_check_host_rocm
if _check_docker; then
    _check_docker_passthrough
fi
_print_next_steps
echo
echo "=== Results: ${PASS} passed, ${WARN} warnings, ${FAIL} failed ==="
[ "${FAIL}" -eq 0 ]
