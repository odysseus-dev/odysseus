#!/bin/sh
# Entrypoint that fixes the #1 self-host footgun: a Docker container
# that runs as root writes root-owned files into bind-mounted host
# volumes, and the host user (or a non-root service user) then can't
# update them — silently breaking skill extraction, prefs saves, mail
# attachments, etc.
#
# Standard PUID/PGID pattern: pick the UID/GID we should drop to,
# chown the writable bind-mounts so existing root-owned content gets
# repaired on every start (idempotent), then exec the real command
# as that user via gosu.
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
GOSU_BIN="$(command -v gosu)"
PYTHON_BIN="$(command -v python)"

# Reuse an existing matching group/user if the host's UID/GID already
# corresponds to one in /etc/passwd (e.g. when the image is rebuilt
# and "odysseus" already exists at the same id). Otherwise create.
if ! getent group "$PGID" >/dev/null 2>&1; then
    groupadd -g "$PGID" odysseus
fi
if ! getent passwd "$PUID" >/dev/null 2>&1; then
    useradd -u "$PUID" -g "$PGID" -M -s /bin/sh -d /app odysseus
fi

ODY_USER="$(getent passwd "$PUID" | cut -d: -f1)"
[ -z "$ODY_USER" ] && ODY_USER=odysseus

# Docker-socket group plumbing. When /var/run/docker.sock is bind-mounted
# (Cookbook uses docker exec to reach sibling containers), the socket is
# owned by root:<host docker gid>. Add the app user to that group and later
# call gosu by username so supplementary groups are retained.
DOCKER_SOCK="${DOCKER_SOCK:-/var/run/docker.sock}"
if [ -S "$DOCKER_SOCK" ]; then
    SOCK_GID="$(stat -c '%g' "$DOCKER_SOCK" 2>/dev/null || echo '')"
    if [ -n "$SOCK_GID" ] && [ "$SOCK_GID" != "0" ]; then
        if ! getent group "$SOCK_GID" >/dev/null 2>&1; then
            groupadd -g "$SOCK_GID" docker_host || true
        fi
        SOCK_GROUP="$(getent group "$SOCK_GID" | cut -d: -f1)"
        if [ -n "$SOCK_GROUP" ]; then
            usermod -aG "$SOCK_GROUP" "$ODY_USER" 2>/dev/null || true
        fi
    fi
fi

mount_root_for() {
    awk -v target="$1" '$5 == target { print $4; exit }' /proc/self/mountinfo 2>/dev/null || true
}

is_broad_mount_root() {
    case "$1" in
        /|/home|/srv|/var|/usr|/opt|/tmp|/mnt|/media)
            return 0
            ;;
    esac
    return 1
}

repair_tree_ownership() {
    dir="$1"
    if [ -d "$dir" ]; then
        find "$dir" -xdev -not -uid "$PUID" -print0 2>/dev/null \
            | xargs -0 -r chown "$PUID:$PGID" 2>/dev/null || true
    fi
}

repair_app_tree_ownership() {
    if [ -d /app ]; then
        find /app -xdev \
            \( -path /app/data -o -path /app/logs -o -path /app/.ssh -o -path /app/.cache -o -path /app/.local \) -prune \
            -o -not -uid "$PUID" -print0 2>/dev/null \
            | xargs -0 -r chown "$PUID:$PGID" 2>/dev/null || true
    fi
}

repair_bind_mount_ownership() {
    dir="$1"
    if [ ! -d "$dir" ]; then
        return
    fi

    mount_root="$(mount_root_for "$dir")"
    if is_broad_mount_root "$mount_root"; then
        echo "Skipping recursive ownership repair for $dir because it maps to broad host path $mount_root" >&2
        chown "$PUID:$PGID" "$dir" 2>/dev/null || true
        return
    fi

    repair_tree_ownership "$dir"
}

# Repair image-owned writable paths without walking into bind-mounted host
# trees, then repair the app-owned mount roots separately.
repair_app_tree_ownership
for dir in /app/data /app/logs /app/.ssh /app/.cache/huggingface /app/.local; do
    repair_bind_mount_ownership "$dir"
done

# Cookbook installs vllm/etc. via `pip install --user`, which pulls
# nvidia-cuda-* wheels into /app/.local but does not set CUDA_HOME or
# symlink /usr/local/cuda. vllm 0.22+ then crashes during engine init
# when FlashInfer tries to JIT a sampler kernel ("Could not find nvcc",
# then "CUDA compiler and toolkit headers are incompatible" on the
# mixed cuda-nvcc 13.3 / cuda-runtime 13.0 wheel combo).
#
# Auto-set CUDA_HOME if a pip-installed nvcc is present, and disable the
# FlashInfer JIT sampler — sampler only, no impact on attention path.
# No-op when vllm isn't installed.
#
# Checked layouts (all are real pip-wheel install paths):
#   nvidia/cu13        — nvidia-nvcc-cu13 (CUDA 13.x wheel style)
#   nvidia/cu12        — nvidia-nvcc-cu12 (CUDA 12.x wheel style)
#   nvidia/cuda_nvcc   — nvidia-cuda-nvcc-cu12 (older cu12 sub-package style)
for cu in \
    /app/.local/lib/python*/site-packages/nvidia/cu13 \
    /app/.local/lib/python*/site-packages/nvidia/cu12 \
    /app/.local/lib/python*/site-packages/nvidia/cuda_nvcc; do
    if [ -x "$cu/bin/nvcc" ]; then
        export CUDA_HOME="$cu"
        break
    fi
done

# Disable the FlashInfer JIT sampler unconditionally — it is sampler-only
# and has no impact on the attention path, but requires nvcc + matching
# CUDA headers at startup. Without this, vLLM crashes with "Could not find
# nvcc" even when the GPU itself is fully visible to the container.
export VLLM_USE_FLASHINFER_SAMPLER="${VLLM_USE_FLASHINFER_SAMPLER:-0}"

# Make Cookbook-installed Python CLIs visible after `pip install --user`.
# vLLM and helper scripts land here because /app is the non-root user's HOME.
export PATH="/app/.local/bin:$PATH"

# Run first-time setup as the app user so data/ files get the right ownership.
# setup.py is idempotent — skips auth.json / .env if they already exist.
# || true so a setup failure never prevents the container from starting.
"$GOSU_BIN" "$ODY_USER" "$PYTHON_BIN" /app/setup.py || true

# Drop root and run the actual app. `gosu` is preferred over `su` /
# `sudo` because it cleans up the process tree (no extra shell layer)
# so signals (SIGTERM from `docker stop`) reach uvicorn directly.
exec "$GOSU_BIN" "$ODY_USER" "$@"
