#!/usr/bin/env bash
set -euo pipefail

family="${1:-}"
target="${2:-}"
patch_root="${3:-patches}"

if [[ -z "$family" ]]; then
  echo "usage: $0 <family> [target] [patch_root]" >&2
  exit 1
fi

if [[ ! -d "$patch_root" ]]; then
  echo "Patch root not found: $patch_root" >&2
  exit 1
fi

shopt -s nullglob

apply_patch_file() {
  local patch_file="$1"
  echo "Applying patch: $patch_file"
  git apply --check "$patch_file"
  git apply "$patch_file"
}

for patch_file in "$patch_root"/common/*.patch; do
  apply_patch_file "$patch_file"
done

if [[ -n "$target" ]]; then
  patch_file="$patch_root/$family/$target.patch"
  if [[ ! -f "$patch_file" ]]; then
    echo "Target patch not found: $patch_file" >&2
    exit 1
  fi
  apply_patch_file "$patch_file"
fi

