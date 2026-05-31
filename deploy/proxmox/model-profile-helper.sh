#!/usr/bin/env bash
set -Eeuo pipefail

TIER="cpu"                  # cpu|gpu_modest|gpu_high
PULL_MODELS="false"
PULL_ALTERNATIVES="false"
EXPECT_OLLAMA="false"

usage() {
  cat <<'EOF'
Usage:
  odysseus-model-profile --tier <cpu|gpu_modest|gpu_high> [--pull-models] [--pull-alternatives] [--expect-ollama]

Purpose:
  Print Odysseus model/runtime recommendations by hardware tier.
  Optionally pull recommended models with Ollama.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tier)
      TIER="${2:-}"; shift 2 ;;
    --pull-models)
      PULL_MODELS="true"; shift ;;
    --pull-alternatives)
      PULL_ALTERNATIVES="true"; shift ;;
    --expect-ollama)
      EXPECT_OLLAMA="true"; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown arg: $1" >&2
      usage
      exit 2 ;;
  esac
done

case "${TIER}" in
  cpu|gpu_modest|gpu_high) ;;
  *)
    echo "Invalid --tier '${TIER}'. Use cpu|gpu_modest|gpu_high." >&2
    exit 2 ;;
esac

if [[ "${EXPECT_OLLAMA}" == "true" ]] && ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama expected but not found in PATH." >&2
  exit 1
fi

recommendations_for_tier() {
  case "${TIER}" in
    cpu)
      cat <<'EOF'
TIER: CPU-only
Recommended local models (Ollama tags):
  1) qwen2.5:3b-instruct-q4_K_M   (primary)
  2) qwen2.5:1.5b-instruct-q4_K_M (fast fallback)
  3) phi3:mini-4k-instruct-q4_K_M (alternative)

Odysseus Settings (suggested):
  - Endpoint type: OpenAI-compatible local server (Ollama)
  - Base URL: http://host.docker.internal:11434/v1
  - Primary model: qwen2.5:3b-instruct-q4_K_M
  - Fallback model: qwen2.5:1.5b-instruct-q4_K_M
EOF
      ;;
    gpu_modest)
      cat <<'EOF'
TIER: Modest GPU (6-12GB VRAM)
Recommended local models (Ollama tags):
  1) qwen2.5:7b-instruct-q4_K_M   (primary)
  2) qwen2.5:3b-instruct-q4_K_M   (fast fallback)
  3) mistral:7b-instruct-v0.3-q4_K_M (alternative)

Odysseus Settings (suggested):
  - Endpoint type: OpenAI-compatible local server (Ollama)
  - Base URL: http://host.docker.internal:11434/v1
  - Primary model: qwen2.5:7b-instruct-q4_K_M
  - Fallback model: qwen2.5:3b-instruct-q4_K_M
EOF
      ;;
    gpu_high)
      cat <<'EOF'
TIER: High GPU (16GB+ VRAM)
Recommended local models (Ollama tags):
  1) qwen2.5:14b-instruct-q4_K_M  (primary)
  2) qwen2.5:7b-instruct-q4_K_M   (fallback)
  3) mixtral:8x7b-instruct-v0.1-q4_K_M (if VRAM budget allows)

Odysseus Settings (suggested):
  - Endpoint type: OpenAI-compatible local server (Ollama)
  - Base URL: http://host.docker.internal:11434/v1
  - Primary model: qwen2.5:14b-instruct-q4_K_M
  - Fallback model: qwen2.5:7b-instruct-q4_K_M
EOF
      ;;
  esac
}

pull_models_for_tier() {
  if ! command -v ollama >/dev/null 2>&1; then
    echo "Ollama not found; skipping model pulls." >&2
    return 0
  fi
  local models=()
  local alternatives=()
  case "${TIER}" in
    cpu)
      models=(qwen2.5:3b-instruct-q4_K_M qwen2.5:1.5b-instruct-q4_K_M)
      alternatives=(phi3:mini-4k-instruct-q4_K_M)
      ;;
    gpu_modest)
      models=(qwen2.5:7b-instruct-q4_K_M qwen2.5:3b-instruct-q4_K_M)
      alternatives=(mistral:7b-instruct-v0.3-q4_K_M)
      ;;
    gpu_high)
      models=(qwen2.5:14b-instruct-q4_K_M qwen2.5:7b-instruct-q4_K_M)
      alternatives=(mixtral:8x7b-instruct-v0.1-q4_K_M)
      ;;
  esac
  if [[ "${PULL_ALTERNATIVES}" == "true" ]]; then
    models+=("${alternatives[@]}")
  fi
  echo "Pulling models with Ollama..."
  for m in "${models[@]}"; do
    echo "  - ollama pull ${m}"
    ollama pull "${m}"
  done
}

recommendations_for_tier
if [[ "${PULL_MODELS}" == "true" ]]; then
  pull_models_for_tier
fi
