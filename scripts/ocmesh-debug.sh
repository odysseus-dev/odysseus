#!/usr/bin/env bash
# ocmesh-debug.sh — run the odysseus agent mesh with the stack health report.
#
# Why a wrapper: ocmesh takes --worktree / --verify / --rounds as CLI flags and
# reads registry settings from $OCMESH_CONFIG_DIR (default ~/.config/ocmesh),
# NOT from the repo's .ocmesh/config.json. The repo file is documentation for
# agents; it is never loaded by the CLI. This script is the working entry point.
#
# Usage:
#   scripts/ocmesh-debug.sh                 # proctree only, no mesh run
#   scripts/ocmesh-debug.sh "task text"     # proctree, then one mesh round
#   scripts/ocmesh-debug.sh -r 3 "task"    # three verified rounds
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

# Per-project ocmesh config so the registry keys this repo cares about do not
# have to be duplicated into the global one.
export OCMESH_CONFIG_DIR="${OCMESH_CONFIG_DIR:-$ROOT/.ocmesh/home}"

ROUNDS=1
if [ "${1:-}" = "-r" ]; then ROUNDS="${2:-1}"; shift 2; fi

# The verify gate is this repo's real gates, not a placeholder. Including the
# health probe means a mesh round cannot "pass" while the stack is down.
VERIFY='python3 -m pytest tests/test_mcp_gateway_pool.py tests/test_mcp_gateway_transport.py tests/test_osint_tool.py --noconftest -q -o asyncio_mode=auto >/dev/null 2>&1 \
  && python3 -m compileall -q app.py core routes src >/dev/null 2>&1 \
  && python3 scripts/generate_env_reference.py >/dev/null 2>&1 \
  && bash scripts/proctree.sh 3 >/dev/null 2>&1'

echo "== stack report =="
bash scripts/proctree.sh 12
echo

TASK="$*"
if [ -z "$TASK" ]; then
  echo "No task given; stack report only."
  echo "Run a mesh round with: scripts/ocmesh-debug.sh -r 2 \"your task\""
  exit 0
fi

echo "== mesh: $TASK =="
echo "   rounds=$ROUNDS worktree=$ROOT"
echo "   agents=planner,worker1,worker2,reviewer"
echo

if ! command -v ocmesh >/dev/null 2>&1; then
  echo "ocmesh not found on PATH." >&2
  exit 127
fi

ocmesh mesh "$TASK" \
  --worktree "$ROOT" \
  --rounds "$ROUNDS" \
  --verify "$VERIFY" \
  --agents planner,worker1,worker2,reviewer