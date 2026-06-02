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

# The Dockerfile pre-creates an "odysseus" user + group at UID/GID 1000.
# For the default case (PUID=PGID=1000) this is a no-op.  When the host
# user overrides PUID/PGID we shift the existing user/group in-place with
# usermod/groupmod so the container still runs under a single named
# identity regardless of the chosen ids.
if getent group odysseus >/dev/null 2>&1; then
    EXISTING_GID=$(getent group odysseus | cut -d: -f3)
    if [ "$EXISTING_GID" != "$PGID" ]; then
        groupmod -g "$PGID" odysseus
    fi
elif ! getent group "$PGID" >/dev/null 2>&1; then
    groupadd -g "$PGID" odysseus
fi

if getent passwd odysseus >/dev/null 2>&1; then
    EXISTING_UID=$(id -u odysseus 2>/dev/null || echo 0)
    if [ "$EXISTING_UID" != "$PUID" ]; then
        usermod -u "$PUID" odysseus
    fi
    usermod -g "$PGID" odysseus
elif ! getent passwd "$PUID" >/dev/null 2>&1; then
    useradd -u "$PUID" -g "$PGID" -M -s /bin/sh -d /app odysseus
fi

# Repair ownership on every writable path the app touches at runtime.
#
# Bind-mounted dirs (/app/data, /app/logs) are the obvious ones, but
# the app ALSO writes inside the image's own source tree at runtime:
#   - services/cache/{search,content}/*  (search cache LRU)
#   - services/search_analytics.json
#   - services/search_engine_error.log
#   - services/tts cache, etc.
# These dirs were created as root during `docker build`, so dropping
# to PUID:PGID would otherwise crash on the first import that tries
# to mkdir them. Chown the whole /app tree — fast (<1s on this size)
# and idempotent via the `-not -uid` filter so we only touch files
# that need fixing.
for dir in /app /app/data /app/logs; do
    if [ -d "$dir" ]; then
        # `find ... -not -uid` keeps this O(touched-files), not
        # O(everything), so terabyte-sized maildirs don't slow startup.
        find "$dir" -not -uid "$PUID" -print0 2>/dev/null \
            | xargs -0 -r chown "$PUID:$PGID" 2>/dev/null || true
    fi
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

# Drop root and run the actual app. `gosu` is preferred over `su` /
# `sudo` because it cleans up the process tree (no extra shell layer)
# so signals (SIGTERM from `docker stop`) reach uvicorn directly.
exec gosu "$PUID:$PGID" "$@"
