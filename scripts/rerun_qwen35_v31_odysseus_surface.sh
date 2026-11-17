#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BASE_URL="${BASE_URL:-http://127.0.0.1:7011}"
ENDPOINT_ID="${ENDPOINT_ID:-8b80db2d}"
SELECTED_ENDPOINT_URL="${SELECTED_ENDPOINT_URL:-http://host.docker.internal:18051/v1}"
MODEL="${MODEL:-qwen35-9b-tool-router-v31-clean-missing-tool-coverage-adapter}"
PROMPT_MODE="${PROMPT_MODE:-compact}"
RUNPOD_HOST="${RUNPOD_HOST:-62.169.159.96}"
RUNPOD_PORT="${RUNPOD_PORT:-28260}"
: "${RUNPOD_KEY:?Set RUNPOD_KEY to the SSH key path}"
LOCAL_PORT="${LOCAL_PORT:-18051}"
REMOTE_PORT="${REMOTE_PORT:-8051}"
TUNNEL_SESSION="${TUNNEL_SESSION:-qwen35_v31_clean_coverage_tunnel}"
STAMP="${STAMP:-$(date -u +%Y%m%d_%H%M%S)}"
OUT_DIR="${OUT_DIR:-$ROOT/data/evals}"
TUNNEL_LOG="${TUNNEL_LOG:-$ROOT/tmp/qwen35_v31_clean_coverage_tunnel_${STAMP}.log}"
CLIENT_RUNTIME_CONTEXT="${CLIENT_RUNTIME_CONTEXT:-{\"surface\":\"tui\",\"session_cwd\":\"$ROOT\",\"sessionCwd\":\"$ROOT\"}}"

cd "$ROOT"
mkdir -p "$OUT_DIR"
mkdir -p "$(dirname "$TUNNEL_LOG")"

need_model() {
  curl -fss --max-time 3 "http://127.0.0.1:${LOCAL_PORT}/v1/models" >/dev/null
}

ensure_tunnel() {
  if need_model; then
    return 0
  fi
  if ! tmux has-session -t "$TUNNEL_SESSION" 2>/dev/null; then
    tmux new-session -d -s "$TUNNEL_SESSION" \
      "exec ssh -N -L 0.0.0.0:${LOCAL_PORT}:127.0.0.1:${REMOTE_PORT} -i '${RUNPOD_KEY}' -p '${RUNPOD_PORT}' -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o ConnectTimeout=8 -o BatchMode=yes root@${RUNPOD_HOST} >>'${TUNNEL_LOG}' 2>&1"
  fi
  for _ in $(seq 1 20); do
    if need_model; then
      return 0
    fi
    sleep 1
  done
  echo "ERROR: model tunnel is not reachable on 127.0.0.1:${LOCAL_PORT}" >&2
  echo "Tunnel log: ${TUNNEL_LOG}" >&2
  tail -40 "$TUNNEL_LOG" >&2 || true
  echo "Tunnel session output:" >&2
  tmux capture-pane -pt "$TUNNEL_SESSION" -S -80 2>/dev/null >&2 || true
  exit 2
}

run_eval() {
  local label="$1"
  local cases="$2"
  shift 2
  local output="$OUT_DIR/qwen35_9b_v31_${label}_${STAMP}.json"
  echo "Running ${label}: ${output}" >&2
  python3 scripts/eval_odysseus_tool_use.py \
    --base-url "$BASE_URL" \
    --endpoint-id "$ENDPOINT_ID" \
    --selected-endpoint-url "$SELECTED_ENDPOINT_URL" \
    --model "$MODEL" \
    --selected-model "$MODEL" \
    --prompt-mode "$PROMPT_MODE" \
    --client-runtime-context "$CLIENT_RUNTIME_CONTEXT" \
    --include-no-tool \
    --include-tui-local \
    --include-email-safety \
    --include-safe-extended \
    --cases "$cases" \
    --output "$output" \
    "$@"
  echo "$output"
}

ensure_tunnel

FOCUS_CASES="web_search_lookup,web_fetch_url,email_accounts_list"
FULL_CASES="notes_list,notes_search,calendar_list,email_list,tasks_list,documents_list,memory_list,research_list,sessions_list,contacts_list,casual_hi,identity_who_are_you,general_map,general_vat,typo_clarification,tui_bash_block,tui_local_project,tui_local_network,tui_local_tests,tui_local_ssh_when_tailscale_down,tui_local_project_discovery_no_web,tui_local_ambiguous_test_now,tui_app_notes_boundary,tui_app_model_picker_boundary,email_send_new_approval,email_reply_draft,email_reply_send_approval,email_archive_latest_approval,email_delete_latest_approval,web_search_lookup,web_fetch_url,email_accounts_list,settings_list,endpoints_list,mcp_list,webhooks_list,skills_list,chat_search,bg_jobs_list"

FOCUS_OUT="$(run_eval terminal_summary_speed_focus_rerun "$FOCUS_CASES")"
FULL_OUT="$(run_eval full_surface_after_terminal_speed_patch_rerun "$FULL_CASES")"

echo
python3 scripts/summarize_odysseus_eval_delta.py \
  "$FOCUS_OUT" \
  --compare data/evals/qwen35_9b_v31_full_surface_split_metrics_20260820_065035.json
echo
python3 scripts/summarize_odysseus_eval_delta.py "$FULL_OUT"
