#!/usr/bin/env bash
set -euo pipefail

# Continue the seven-model Epictetus matrix after the already-running baseline.
root=$(cd "$(dirname "$0")/.." && pwd)
baseline_session=regular-local-epictetus-baseline-20260910
models='8-bit,DeepSeek-V4-Flash-0731-AWQ,Qwen3.8-27B-MTP-8bit,Qwen3.8-27B-mlx-4Bit,Qwen3.8-27B-mlx-8Bit,mlx-community--Qwen3.6-27B-MTP-bf16,qwen36-27b-mlx-8bit'

while tmux has-session -t "$baseline_session" 2>/dev/null; do sleep 15; done
cd "$root"
MODELS="$models" WORKERS=1 TURN_TIMEOUT_MS=120000 PROFILE=conversation \
REPORT_PATH=reports/regular-model-local-epictetus-conversation-20260910.json \
node scripts/verify_regular_model_tools.mjs

MODELS="$models" WORKERS=1 TURN_TIMEOUT_MS=120000 PROFILE=switchback \
REPORT_PATH=reports/regular-model-local-epictetus-switchback-20260910.json \
node scripts/verify_regular_model_tools.mjs
