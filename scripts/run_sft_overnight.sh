#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

OWNER="${OWNER:-sft_maya_ops}"
DOMAINS="${DOMAINS:-email,notes,calendar}"
PER_DOMAIN="${PER_DOMAIN:-140}"
TARGET_CLEAN_PER_DOMAIN="${TARGET_CLEAN_PER_DOMAIN:-100}"
ROUNDS="${ROUNDS:-12}"
PROCESS_TIMEOUT="${PROCESS_TIMEOUT:-25m}"
CASE_TIMEOUT="${CASE_TIMEOUT:-90}"
STREAM_TIMEOUT="${STREAM_TIMEOUT:-60}"
SLEEP_SECONDS="${SLEEP_SECONDS:-0.2}"
: "${PASSWORD:?Set PASSWORD explicitly for isolated QA fixture authentication}"
ENDPOINT="${ENDPOINT:-https://openrouter.ai/api/v1/chat/completions}"
ENDPOINT_ID="${ENDPOINT_ID:-f3904562}"
MODEL="${MODEL:-moonshotai/kimi-k3}"
BASE_URL="${BASE_URL:-http://127.0.0.1:7011}"

RUN_ID="${RUN_ID:-sft_overnight_${OWNER}_$(date -u +%Y%m%d_%H%M%S)}"
OUT_DIR="${OUT_DIR:-data/evals/$RUN_ID}"
LOG="${LOG:-data/evals/$RUN_ID.log}"
PID_FILE="${PID_FILE:-data/evals/$RUN_ID.pid}"

mkdir -p "$(dirname "$LOG")"
echo "$$" > "$PID_FILE"

for round in $(seq 1 "$ROUNDS"); do
  printf '{"round":%s,"owner":"%s","started_at":"%s"}\n' "$round" "$OWNER" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$LOG"
  set +e
  timeout "$PROCESS_TIMEOUT" .venv/bin/python scripts/run_sft_overnight_fixture_flows.py \
    --base-url "$BASE_URL" \
    --owner "$OWNER" \
    --password "$PASSWORD" \
    --endpoint "$ENDPOINT" \
    --endpoint-id "$ENDPOINT_ID" \
    --model "$MODEL" \
    --domains "$DOMAINS" \
    --per-domain "$PER_DOMAIN" \
    --target-clean-per-domain "$TARGET_CLEAN_PER_DOMAIN" \
    --case-timeout "$CASE_TIMEOUT" \
    --timeout "$STREAM_TIMEOUT" \
    --sleep "$SLEEP_SECONDS" \
    --out-dir "$OUT_DIR" >> "$LOG" 2>&1
  code=$?
  set -e
  printf '{"round":%s,"owner":"%s","exit_code":%s,"ended_at":"%s"}\n' "$round" "$OWNER" "$code" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$LOG"
  if [ "$code" -eq 0 ]; then
    exit 0
  fi
  sleep 10
done

exit 1
