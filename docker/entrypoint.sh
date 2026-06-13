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

# Reuse an existing matching group/user if the host's UID/GID already
# corresponds to one in /etc/passwd (e.g. when the image is rebuilt
# and "odysseus" already exists at the same id). Otherwise create.
if ! getent group "$PGID" >/dev/null 2>&1; then
    groupadd -g "$PGID" odysseus
fi
if ! getent passwd "$PUID" >/dev/null 2>&1; then
    useradd -u "$PUID" -g "$PGID" -M -s /bin/sh -d /app odysseus
fi

# Repair ownership for runtime-writable paths without recursively walking the
# image source tree. The Dockerfile owns /app as the default runtime user; on
# startup we only need to repair bind mounts that may have been created by root
# on the host. Large Cookbook/Hugging Face caches are persisted separately and
# are intentionally not recursed on every boot.
chown_path() {
    path="$1"
    if [ -e "$path" ]; then
        chown "$PUID:$PGID" "$path" 2>/dev/null || true
    fi
}

repair_tree() {
    dir="$1"
    if [ -d "$dir" ]; then
        find "$dir" -not -uid "$PUID" -print0 2>/dev/null \
            | xargs -0 -r chown "$PUID:$PGID" 2>/dev/null || true
    fi
}

# Let setup.py create /app/.env, and let first-run cache installs create files
# in these mount roots, without scanning their existing contents.
for path in \
    /app \
    /app/.cache \
    /app/.cache/huggingface \
    /app/.local \
    /app/data/local \
    /app/data/huggingface; do
    chown_path "$path"
done

# /app/data is host-editable app state. Prune the large cache subtrees that are
# also mounted at /app/.local and /app/.cache/huggingface.
if [ -d /app/data ]; then
    find /app/data \
        \( -path /app/data/local -o -path /app/data/local/\* \
        -o -path /app/data/huggingface -o -path /app/data/huggingface/\* \) -prune \
        -o -not -uid "$PUID" -print0 2>/dev/null \
        | xargs -0 -r chown "$PUID:$PGID" 2>/dev/null || true
fi

repair_tree /app/logs
repair_tree /app/.ssh

# Escape hatch for installations that already have root-owned package/model
# caches. It is intentionally opt-in because these trees can be multi-GB on
# Docker Desktop/WSL and re-chowning them can make every boot appear hung.
case "${ODYSSEUS_CHOWN_CACHE_TREES:-false}" in
    1|true|TRUE|yes|YES)
        repair_tree /app/.local
        repair_tree /app/.cache/huggingface
        repair_tree /app/data/local
        repair_tree /app/data/huggingface
        ;;
esac

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
gosu "$PUID:$PGID" python /app/setup.py || true

# Drop root and run the actual app. `gosu` is preferred over `su` /
# `sudo` because it cleans up the process tree (no extra shell layer)
# so signals (SIGTERM from `docker stop`) reach uvicorn directly.
exec gosu "$PUID:$PGID" "$@"
