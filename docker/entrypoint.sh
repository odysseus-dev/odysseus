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

# Optional SOPS-encrypted secrets at rest. When /app/secrets.env is
# present it MUST be SOPS-encrypted (detected via the `sops_version`
# trailing line). On encrypted, we exec via `sops exec-env` so secrets
# enter the process as env vars JIT — plaintext never touches the
# container filesystem. setup.py runs INSIDE the wrap so it sees
# secrets like ODYSSEUS_ADMIN_PASSWORD (reviewer feedback on #236).
# On plaintext, we refuse to start — a plaintext secrets.env in /app
# is almost always a packaging mistake. See SECURITY.md ("Encrypting
# Secrets At Rest") for the user-side workflow.
SECRETS_FILE="/app/secrets.env"
if [ -f "$SECRETS_FILE" ]; then
    if ! grep -q '^sops_version' "$SECRETS_FILE"; then
        echo "entrypoint: $SECRETS_FILE exists but is not SOPS-encrypted; refusing to start" >&2
        echo "entrypoint: encrypt it with: sops -e -i secrets.env" >&2
        exit 1
    fi
    if ! command -v sops >/dev/null 2>&1; then
        echo "entrypoint: $SECRETS_FILE is SOPS-encrypted but 'sops' is not on PATH" >&2
        exit 1
    fi
    if [ -z "${SOPS_AGE_KEY_FILE:-}" ] && [ -z "${SOPS_AGE_KEY:-}" ]; then
        echo "entrypoint: $SECRETS_FILE is SOPS-encrypted but neither SOPS_AGE_KEY_FILE nor SOPS_AGE_KEY is set" >&2
        exit 1
    fi
    # `sops exec-env` takes a single command string (passed to sh -c),
    # not argv passthrough — so we single-quote each positional arg to
    # preserve word boundaries when sh re-tokenises.
    cmd=""
    for a in "$@"; do
        qa=$(printf '%s' "$a" | sed "s/'/'\\\\''/g")
        cmd="$cmd '$qa'"
    done
    # Chain: setup.py inherits the SOPS-injected env (so
    # ODYSSEUS_ADMIN_PASSWORD reaches it), then exec replaces the shell
    # with the real app. setup.py failure is non-fatal via `|| true` as
    # in the non-SOPS path below.
    #
    # --same-process: without it, `sops exec-env` fork/waits and stays as
    # PID 1, so `docker stop`'s SIGTERM hits sops instead of uvicorn —
    # graceful shutdown gets skipped. With it, sops execve's into
    # `sh -c "<chain>"`, the chain's final `exec uvicorn` then replaces
    # sh, and uvicorn ends up as PID 1 / the signal target — matching
    # the no-SOPS branch's signal contract.
    exec "$GOSU_BIN" "$PUID:$PGID" sops exec-env --same-process "$SECRETS_FILE" \
        "$PYTHON_BIN /app/setup.py || true; exec$cmd"
fi

# No-SOPS path. Run first-time setup as the app user so data/ files get
# the right ownership. setup.py is idempotent — skips auth.json / .env
# if they already exist. || true so a setup failure never prevents the
# container from starting.
"$GOSU_BIN" "$PUID:$PGID" "$PYTHON_BIN" /app/setup.py || true

# Drop root and run the actual app. `gosu` is preferred over `su` /
# `sudo` because it cleans up the process tree (no extra shell layer)
# so signals (SIGTERM from `docker stop`) reach uvicorn directly.
exec "$GOSU_BIN" "$PUID:$PGID" "$@"
