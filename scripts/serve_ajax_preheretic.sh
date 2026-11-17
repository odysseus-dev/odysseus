#!/usr/bin/env bash
# Run on Ajax. Pre-heretic BF16, four TP2 replicas; no weight modifications.
set -euo pipefail
: "${VLLM_BIN:?Set VLLM_BIN to the absolute vLLM executable path}"
case "$VLLM_BIN" in
  /*) ;;
  *) printf '%s\n' 'VLLM_BIN must be an absolute executable path' >&2; exit 2 ;;
esac
case "$VLLM_BIN" in
  *:*|*$'\n'*) printf '%s\n' 'VLLM_BIN must not contain PATH separators or newlines' >&2; exit 2 ;;
esac
if [ ! -f "$VLLM_BIN" ] || [ ! -x "$VLLM_BIN" ]; then
  printf '%s\n' 'VLLM_BIN must name an existing executable file' >&2
  exit 2
fi
VLLM_BIN_DIR="${VLLM_BIN%/*}"
export PATH="${VLLM_BIN_DIR:-/}:/usr/local/bin:/usr/bin:/bin"
export NCCL_P2P_DISABLE=1
# Installed FlashInfer sampling JIT fails against the installed CUB headers.
# vLLM's native sampler avoids that optional kernel compilation.
export VLLM_USE_FLASHINFER_SAMPLER=0
exec "${VLLM_BIN:-vllm}" serve \
  "${MODEL_PATH:?Set MODEL_PATH explicitly}" \
  --served-model-name odysseus-qwen3.5-tools-pre-heretic \
  --host 0.0.0.0 --port 19184 --dtype bfloat16 \
  --tensor-parallel-size 2 --data-parallel-size 4 --data-parallel-size-local 4 \
  --distributed-executor-backend mp --disable-custom-all-reduce \
  --gpu-memory-utilization 0.9 --max-model-len 16384 --max-num-seqs 8 \
  --enforce-eager --trust-remote-code --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder --limit-mm-per-prompt '{"image":3,"video":0}' \
  --gdn-prefill-backend triton --disable-log-stats
