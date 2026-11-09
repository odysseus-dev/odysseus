#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

OWNER="${OWNER:-sft_maya_ops}" exec scripts/run_sft_overnight.sh "$@"
