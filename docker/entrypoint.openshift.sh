#!/bin/sh
set -e

export HOME="${HOME:-/app}"
export PATH="/app/.local/bin:$PATH"
export VLLM_USE_FLASHINFER_SAMPLER="${VLLM_USE_FLASHINFER_SAMPLER:-0}"

mkdir -p \
  /app/data \
  /app/logs \
  /app/.ssh \
  /app/.cache/huggingface \
  /app/.local

# setup.py is idempotent and creates the first admin account from env vars.
python /app/setup.py

if [ "${AUTH_ENABLED:-true}" != "false" ] && [ ! -s "${ODYSSEUS_DATA_DIR:-/app/data}/auth.json" ]; then
  echo "Odysseus auth is enabled, but setup did not create ${ODYSSEUS_DATA_DIR:-/app/data}/auth.json" >&2
  exit 1
fi

exec "$@"
